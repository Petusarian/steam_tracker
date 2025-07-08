import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import json
import re
import os
from datetime import datetime, timezone, timedelta
import logging
import time

# === CONFIGURATION ===
SPREADSHEET_NAME = 'STEAM_TRACKER'
# Use Streamlit secrets for service account credentials
SERVICE_ACCOUNT_INFO = st.secrets["gcp_service_account"]
SERVICE_ACCOUNT_SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]



# === SETUP ===
st.set_page_config(
    page_title="Steam Game Tracker",
    page_icon="🎮",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for better media display and favorites
st.markdown("""
<style>
.game-header-image {
    cursor: pointer;
    transition: opacity 0.3s;
}
.game-header-image:hover {
    opacity: 0.8;
}
.scrollable-screenshots {
    display: flex;
    overflow-x: auto;
    padding: 10px 0;
    gap: 10px;
}
.scrollable-screenshots img {
    flex-shrink: 0;
    max-height: 200px;
    border-radius: 8px;
}
.media-container {
    display: flex;
    gap: 20px;
}
.video-section {
    flex: 1;
}
.screenshot-section {
    flex: 1;
}
.date-added-badge {
    background-color: #f0f2f6;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.8em;
    color: #666;
}
</style>
""", unsafe_allow_html=True)

# === FAVORITES & LISTS STATE MANAGEMENT ===
def init_favorites_state():
    """Initialize favorites and lists in session state."""
    if 'favorites' not in st.session_state:
        st.session_state.favorites = []
    if 'custom_lists' not in st.session_state:
        st.session_state.custom_lists = {}
    if 'selected_list_filter' not in st.session_state:
        st.session_state.selected_list_filter = "All"

def sync_with_localstorage():
    """JavaScript to sync localStorage with Streamlit session state."""
    return st.markdown("""
    <script>
    // Get data from localStorage and sync with Streamlit
    function syncFromLocalStorage() {
        try {
            const favorites = localStorage.getItem('steamTracker_favorites');
            const customLists = localStorage.getItem('steamTracker_customLists');
            
            if (favorites || customLists) {
                // Create hidden input to pass data to Streamlit
                let syncDiv = document.getElementById('localStorage-sync');
                if (!syncDiv) {
                    syncDiv = document.createElement('div');
                    syncDiv.id = 'localStorage-sync';
                    syncDiv.style.display = 'none';
                    document.body.appendChild(syncDiv);
                }
                
                syncDiv.setAttribute('data-favorites', favorites || '[]');
                syncDiv.setAttribute('data-custom-lists', customLists || '{}');
            }
        } catch (e) {
            console.log('LocalStorage sync error:', e);
        }
    }
    
    // Save data to localStorage
    function saveToLocalStorage(favorites, customLists) {
        try {
            localStorage.setItem('steamTracker_favorites', JSON.stringify(favorites));
            localStorage.setItem('steamTracker_customLists', JSON.stringify(customLists));
        } catch (e) {
            console.log('LocalStorage save error:', e);
        }
    }
    
    // Execute sync
    syncFromLocalStorage();
    
    // Make save function globally available
    window.saveToLocalStorage = saveToLocalStorage;
    </script>
    """, unsafe_allow_html=True)

def get_localstorage_data():
    """Try to get localStorage data via JavaScript."""
    # The JavaScript sets data attributes on a hidden div
    # This is a simple way to get localStorage data into Streamlit
    sync_with_localstorage()
    
    # Check if we have stored data to load (only on first run)
    if 'localStorage_loaded' not in st.session_state:
        st.session_state.localStorage_loaded = True
        # In a real implementation, we'd need a Streamlit component to properly bridge this
        # For now, we'll use session state as the source of truth

def save_to_localstorage():
    """Save current session state to localStorage."""
    st.markdown(f"""
    <script>
    if (window.saveToLocalStorage) {{
        window.saveToLocalStorage(
            {json.dumps(st.session_state.favorites)},
            {json.dumps(st.session_state.custom_lists)}
        );
    }}
    </script>
    """, unsafe_allow_html=True)

def get_game_id(game):
    """Get a unique ID for a game (using AppID or name as fallback)."""
    if pd.notna(game.get('AppID')):
        return int(game['AppID'])
    else:
        # Fallback to hash of name if AppID not available
        return hash(game['Name'])

def is_game_favorited(game_id):
    """Check if a game is in favorites."""
    return any(fav.get('id') == game_id for fav in st.session_state.favorites)

def is_game_in_list(game_id, list_name):
    """Check if a game is in a specific custom list."""
    if list_name not in st.session_state.custom_lists:
        return False
    return any(game.get('id') == game_id for game in st.session_state.custom_lists[list_name])

def create_favorite_button(game):
    """Create a favorite button for a game."""
    game_id = get_game_id(game)
    is_favorited = is_game_favorited(game_id)
    
    button_text = "💙 Favorited" if is_favorited else "🤍 Add to Favorites"
    
    if st.button(button_text, key=f"fav_btn_{game_id}", use_container_width=True):
        if is_favorited:
            # Remove from favorites
            st.session_state.favorites = [
                fav for fav in st.session_state.favorites 
                if fav.get('id') != game_id
            ]
        else:
            # Add to favorites
            st.session_state.favorites.append({
                'id': game_id,
                'name': game['Name'],
                'dateAdded': datetime.now().isoformat()
            })
        
        # Save to localStorage
        save_to_localstorage()
        st.rerun()

def create_list_management_buttons(game):
    """Create buttons for adding/removing games from custom lists."""
    game_id = get_game_id(game)
    
    # Get available lists
    available_lists = list(st.session_state.custom_lists.keys())
    
    if available_lists:
        # Dropdown to select list
        selected_list = st.selectbox(
            "Add to List:",
            ["Select a list..."] + available_lists,
            key=f"list_select_{game_id}"
        )
        
        if selected_list != "Select a list...":
            col1, col2 = st.columns(2)
            
            with col1:
                if st.button(f"➕ Add", key=f"add_to_list_{game_id}_{selected_list}"):
                    if selected_list not in st.session_state.custom_lists:
                        st.session_state.custom_lists[selected_list] = []
                    
                    if not is_game_in_list(game_id, selected_list):
                        st.session_state.custom_lists[selected_list].append({
                            'id': game_id,
                            'name': game['Name'],
                            'dateAdded': datetime.now().isoformat()
                        })
                        save_to_localstorage()  # Save to localStorage
                        st.success(f"Added to {selected_list}!")
                        st.rerun()
                    else:
                        st.warning("Already in this list!")
            
            with col2:
                if is_game_in_list(game_id, selected_list):
                    if st.button(f"➖ Remove", key=f"remove_from_list_{game_id}_{selected_list}"):
                        st.session_state.custom_lists[selected_list] = [
                            g for g in st.session_state.custom_lists[selected_list] 
                            if g.get('id') != game_id
                        ]
                        save_to_localstorage()  # Save to localStorage
                        st.success(f"Removed from {selected_list}!")
                        st.rerun()

def filter_by_favorites_and_lists(df, filter_type, selected_list=None):
    """Filter dataframe by favorites or custom lists."""
    if filter_type == "Favorites":
        favorite_ids = [fav.get('id') for fav in st.session_state.favorites]
        if favorite_ids:
            game_ids = df.apply(get_game_id, axis=1)
            return df[game_ids.isin(favorite_ids)]
        else:
            return pd.DataFrame()  # Empty if no favorites
    
    elif filter_type == "Custom List" and selected_list:
        if selected_list in st.session_state.custom_lists:
            list_game_ids = [game.get('id') for game in st.session_state.custom_lists[selected_list]]
            if list_game_ids:
                game_ids = df.apply(get_game_id, axis=1)
                return df[game_ids.isin(list_game_ids)]
        return pd.DataFrame()  # Empty if list doesn't exist or is empty
    
    return df  # Return original if "All"

# === SMART CACHING FUNCTIONS ===

def get_croatian_time():
    """Get current time in Croatian timezone (CET/CEST)."""
    # Croatian timezone is UTC+1 (CET) in winter, UTC+2 (CEST) in summer
    # We'll use a simple approach with UTC offset
    utc_now = datetime.now(timezone.utc)
    
    # Determine if it's daylight saving time (rough approximation)
    # DST in Europe typically runs from last Sunday in March to last Sunday in October
    year = utc_now.year
    march_last_sunday = datetime(year, 3, 31) - timedelta(days=datetime(year, 3, 31).weekday() + 1 % 7)
    october_last_sunday = datetime(year, 10, 31) - timedelta(days=datetime(year, 10, 31).weekday() + 1 % 7)
    
    # Simple DST check (not perfect but good enough)
    if march_last_sunday <= utc_now.replace(tzinfo=None) < october_last_sunday:
        croatia_offset = timedelta(hours=2)  # CEST (UTC+2)
    else:
        croatia_offset = timedelta(hours=1)   # CET (UTC+1)
    
    return utc_now + croatia_offset

def should_update_cache():
    """Check if we should update the cache based on Croatian time and last update."""
    if 'last_cache_update' not in st.session_state:
        return True
    
    croatia_time = get_croatian_time()
    last_update = st.session_state.get('last_cache_update')
    
    if not last_update:
        return True
    
    # Check if it's past 20:00 Croatian time and we haven't updated today
    if croatia_time.hour >= 20:
        # Check if last update was before today's 20:00
        today_update_time = croatia_time.replace(hour=20, minute=0, second=0, microsecond=0)
        if last_update < today_update_time:
            return True
    
    return False

def get_cache_key():
    """Generate a cache key that changes daily at 20:00 Croatian time."""
    croatia_time = get_croatian_time()
    
    # If it's before 20:00, use yesterday's date
    # If it's after 20:00, use today's date
    if croatia_time.hour >= 20:
        cache_date = croatia_time.date()
    else:
        cache_date = (croatia_time - timedelta(days=1)).date()
    
    return f"steam_data_{cache_date}"

def get_cached_data():
    """Get cached data if available and valid."""
    cache_key = get_cache_key()
    
    # Initialize cache storage if not exists
    if 'data_cache' not in st.session_state:
        st.session_state.data_cache = {}
    
    # Check if we have cached data for current cache key
    if cache_key in st.session_state.data_cache:
        cached_data = st.session_state.data_cache[cache_key]
        return cached_data.get('data'), cached_data.get('timestamp')
    
    return None, None

def set_cached_data(data):
    """Store data in cache with current timestamp."""
    cache_key = get_cache_key()
    croatia_time = get_croatian_time()
    
    # Initialize cache storage if not exists
    if 'data_cache' not in st.session_state:
        st.session_state.data_cache = {}
    
    # Store data with timestamp
    st.session_state.data_cache[cache_key] = {
        'data': data,
        'timestamp': croatia_time
    }
    
    # Update last cache update timestamp
    st.session_state.last_cache_update = croatia_time
    
    # Clean up old cache entries (keep only current and previous day)
    keys_to_remove = []
    for key in st.session_state.data_cache.keys():
        if key != cache_key and not key.endswith(str((croatia_time - timedelta(days=1)).date())):
            keys_to_remove.append(key)
    
    for key in keys_to_remove:
        del st.session_state.data_cache[key]

def load_steam_data() -> pd.DataFrame:
    """Load Steam game data from Google Sheets with 24-hour Croatian time caching."""
    # Check if we have valid cached data
    cached_data, cache_timestamp = get_cached_data()
    
    if cached_data is not None and not should_update_cache():
        # Return cached data if valid
        return cached_data
    
    # If no cached data or cache expired, load fresh data
    try:
        # Fix for Streamlit Community Cloud - properly handle service account credentials
        
        # Try to load as JSON string first (alternative method)
        if isinstance(SERVICE_ACCOUNT_INFO, str):
            try:
                svc_info = json.loads(SERVICE_ACCOUNT_INFO)
            except json.JSONDecodeError:
                st.error("Invalid JSON format in service account credentials")
                return pd.DataFrame()
        else:
            # Load as dict (original method)
            svc_info = dict(SERVICE_ACCOUNT_INFO)  # Convert SecretsDict to a regular dict
        
        # Check if we have the required fields
        required_fields = ['type', 'project_id', 'private_key_id', 'private_key', 'client_email', 'client_id']
        missing_fields = [field for field in required_fields if field not in svc_info]
        if missing_fields:
            st.error(f"Missing required fields: {missing_fields}")
            return pd.DataFrame()
        
        # Ensure private_key newlines are correct for JWT
        if isinstance(svc_info.get('private_key'), str):
            private_key = svc_info['private_key']
            # Handle multiple possible newline formats
            if '\\n' in private_key:
                private_key = private_key.replace('\\n', '\n')
            # Ensure proper BEGIN/END formatting
            if not private_key.startswith('-----BEGIN PRIVATE KEY-----'):
                # Remove any accidental prefix characters
                private_key = private_key.strip()
            svc_info['private_key'] = private_key
        
        # Ensure all required fields are strings
        for field in required_fields:
            if field in svc_info:
                svc_info[field] = str(svc_info[field])
        
        # Create credentials from info
        creds = Credentials.from_service_account_info(
            svc_info,
            scopes=SERVICE_ACCOUNT_SCOPES
        )
        
        client = gspread.authorize(creds)
        
        sheet = client.open(SPREADSHEET_NAME)

        try:
            master_sheet = sheet.worksheet('Steam_Master')
            data = master_sheet.get_all_records()
            df = pd.DataFrame(data)
            if df.empty:
                st.warning("No data found in the Steam_Master sheet.")
                return pd.DataFrame()

            # Convert data types
            if 'AppID' in df.columns:
                df['AppID'] = pd.to_numeric(df['AppID'], errors='coerce')
            if 'DateAdded' in df.columns:
                df['DateAdded'] = pd.to_datetime(df['DateAdded'], errors='coerce')
            # Convert boolean fields
            for field in ['Demo', 'IsDemo', 'IsComingSoon', 'IsPlaceholderDate']:
                if field in df.columns:
                    df[field] = df[field].astype(str).str.lower().isin(['true','1','yes'])
            
            # Cache the successfully loaded data
            set_cached_data(df)
            
            return df
        except gspread.WorksheetNotFound:
            st.error("Steam_Master worksheet not found. Please run the data script first.")
            return pd.DataFrame()
    except Exception as e:
        st.error(f"Error loading data: {e}")
        # Show more detailed error information
        import traceback
        st.error(f"Detailed error: {traceback.format_exc()}")
        
        # Return cached data if available, even if it's older
        if cached_data is not None:
            st.warning("Using cached data due to loading error.")
            return cached_data
        
        return pd.DataFrame()

def create_description_search_text(row):
    """Combine all description fields for comprehensive text search."""
    text_parts = []
    
    # Add name
    if pd.notna(row.get('Name')):
        text_parts.append(str(row['Name']))
    
    # Add descriptions (using DetailedDescription as primary, falling back to others)
    description_fields = ['DetailedDescription', 'AboutTheGame', 'ShortDescription']
    for field in description_fields:
        if pd.notna(row.get(field)) and str(row[field]).strip():
            text_parts.append(str(row[field]))
            break  # Use only the first available description to avoid duplication
    
    # Add genres and categories
    for field in ['Genres', 'Categories']:
        if pd.notna(row.get(field)) and str(row[field]).strip():
            text_parts.append(str(row[field]))
    
    return ' '.join(text_parts).lower()

def filter_games_by_keywords(df, keywords):
    """Filter games based on keywords found in descriptions, name, genres, etc."""
    if not keywords or len(df) == 0:
        return df
    
    # Create searchable text for each game
    df_copy = df.copy()
    df_copy['search_text'] = df_copy.apply(create_description_search_text, axis=1)
    
    # Filter based on keywords
    keyword_list = [k.strip().lower() for k in keywords.split(',') if k.strip()]
    
    if not keyword_list:
        return df
    
    mask = pd.Series([False] * len(df_copy))
    
    for keyword in keyword_list:
        keyword_mask = df_copy['search_text'].str.contains(keyword, case=False, na=False, regex=False)
        mask = mask | keyword_mask
    
    filtered_df = df_copy[mask].copy()
    
    # Drop the helper column
    if 'search_text' in filtered_df.columns:
        filtered_df = filtered_df.drop('search_text', axis=1)
    
    return filtered_df

def create_header_image(game):
    """Display the header image for a game."""
    header_image = game.get('HeaderImage', '')
    
    if not header_image:
        return
    
    # Show header image without false hover promises
    st.image(header_image, caption=game['Name'], use_container_width=True)

def display_enhanced_media_gallery(screenshots_json, movies_json):
    """Display an enhanced media gallery with proper organization."""
    st.markdown("""
    <div class="media-container">
    """, unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("### 🎬 Trailers & Videos")
        try:
            movies = json.loads(movies_json) if movies_json else []
            if movies:
                # Sort movies: highlights first, then by name
                sorted_movies = sorted(movies, key=lambda x: (not x.get('highlight', False), x.get('name', '')))
                
                for i, movie in enumerate(sorted_movies):
                    with st.expander(f"🎥 {movie.get('name', f'Video {i+1}')}", expanded=(i == 0)):
                        if movie.get('video_url'):
                            st.video(movie['video_url'])
                        elif movie.get('thumbnail'):
                            st.image(movie['thumbnail'], caption=movie.get('name', 'Video'))
            else:
                st.info("No trailers available")
        except (json.JSONDecodeError, TypeError) as e:
            st.info("No trailers available")
    
    with col2:
        st.markdown("### 🖼️ Screenshots")
        try:
            screenshots = json.loads(screenshots_json) if screenshots_json else []
            if screenshots:
                # Create tabs for better organization
                if len(screenshots) > 3:
                    # Use tabs for many screenshots
                    tab_names = [f"Screenshot {i+1}" for i in range(min(len(screenshots), 5))]
                    tabs = st.tabs(tab_names)
                    
                    for i, (tab, screenshot) in enumerate(zip(tabs, screenshots[:5])):
                        with tab:
                            if screenshot.get('full'):
                                st.image(screenshot['full'], use_container_width=True)
                            elif screenshot.get('thumbnail'):
                                st.image(screenshot['thumbnail'], use_container_width=True)
                else:
                    # Show all screenshots if 3 or fewer
                    for i, screenshot in enumerate(screenshots):
                        if screenshot.get('full'):
                            st.image(screenshot['full'], caption=f"Screenshot {i+1}", use_container_width=True)
                        elif screenshot.get('thumbnail'):
                            st.image(screenshot['thumbnail'], caption=f"Screenshot {i+1}", use_container_width=True)
            else:
                st.info("No screenshots available")
        except (json.JSONDecodeError, TypeError) as e:
            st.info("No screenshots available")
    
    st.markdown("</div>", unsafe_allow_html=True)

def load_steam_tags():
    """Load comprehensive Steam tags from file."""
    try:
        with open('Steam_Tags_List.txt', 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Parse the tags - they're separated by newlines
        all_tags = []
        for line in content.strip().split('\n'):
            tag = line.strip()
            if tag and not tag.startswith('#'):  # Skip empty lines and comments
                all_tags.append(tag)
        
        # Clean up tags and create mapping for better matching
        steam_tags = set()
        tag_variations = {}
        
        for tag in all_tags:
            clean_tag = tag.strip()
            if clean_tag:
                steam_tags.add(clean_tag.lower())
                # Create variations for better matching
                tag_variations[clean_tag.lower()] = clean_tag
                
                # Add hyphenated/dash versions
                if '-' in clean_tag:
                    space_version = clean_tag.replace('-', ' ')
                    tag_variations[space_version.lower()] = clean_tag
                    steam_tags.add(space_version.lower())
                elif ' ' in clean_tag:
                    dash_version = clean_tag.replace(' ', '-')
                    tag_variations[dash_version.lower()] = clean_tag
                    steam_tags.add(dash_version.lower())
                
                # Add common variations
                if '&' in clean_tag:
                    and_version = clean_tag.replace('&', 'and')
                    tag_variations[and_version.lower()] = clean_tag
                    steam_tags.add(and_version.lower())
        
        return steam_tags, tag_variations
    except FileNotFoundError:
        # Fallback to basic tags if file not found
        basic_tags = {'action', 'adventure', 'rpg', 'strategy', 'simulation', 'indie', 'casual'}
        return basic_tags, {tag: tag.title() for tag in basic_tags}

def display_game_tags(game):
    """Displays the real, community-voted Steam Tags for a game."""
    tags_str = game.get('CommunityTags', '')
    if not tags_str or not isinstance(tags_str, str):
        st.caption("No community tags available.")
        return

    tags_list = [tag.strip() for tag in tags_str.split(',')]
    
    if not tags_list:
        st.caption("No community tags available.")
        return

    # Create formatted tag list with top 5 bolded
    formatted_tags = []
    # Display a reasonable number of tags, e.g., first 10, to fit on two lines
    display_count = min(10, len(tags_list)) 
    
    for i in range(display_count):
        tag = tags_list[i]
        if i < 5:  # Top 5 are most popular
            formatted_tags.append(f"**{tag}**")
        else:
            formatted_tags.append(tag)
    
    remaining = len(tags_list) - display_count
    if remaining > 0:
        tag_display = f"🎯 {' • '.join(formatted_tags)} (+{remaining} more)"
    else:
        tag_display = f"🎯 {' • '.join(formatted_tags)}"
    
    st.info(tag_display)

def get_primary_description(game):
    """Get the primary description from DetailedDescription."""
    # Check DetailedDescription
    detailed_desc = game.get('DetailedDescription', '')
    if pd.notna(detailed_desc) and str(detailed_desc).strip():
        return str(detailed_desc).strip()
    
    return None

def format_date_added(date_added):
    """Format the DateAdded field for display."""
    if pd.isna(date_added):
        return "Unknown"
    
    try:
        if isinstance(date_added, str):
            # Parse the UTC datetime string
            dt = datetime.fromisoformat(date_added.replace('UTC', '').strip())
        else:
            dt = date_added
        
        # Format as readable date
        return dt.strftime('%Y-%m-%d %H:%M')
    except:
        return str(date_added)

def get_demo_status(game):
    """Get demo status for a game."""
    status = {
        'has_demo': False
    }
    
    # Check for demo availability
    if game.get('Demo', False) or game.get('IsDemo', False):
        status['has_demo'] = True
    
    return status


def get_release_status_display(game):
    """Get enhanced release status display information for a game."""
    release_status = game.get('ReleaseStatus', 'unknown')
    is_coming_soon = game.get('IsComingSoon', False)
    is_placeholder = game.get('IsPlaceholderDate', False)
    release_date = game.get('ReleaseDate', 'Unknown')
    
    # Map release status to display information
    if release_status == 'released':
        return {
            'status_text': 'Released',
            'status_emoji': '✅',
            'status_color': 'green',
            'date_text': release_date,
            'badge_text': '✅ Released'
        }
    elif release_status == 'coming_soon':
        prefix = '~' if is_placeholder else ''
        return {
            'status_text': 'Coming Soon',
            'status_emoji': '🔜',
            'status_color': 'orange',
            'date_text': f"{prefix}{release_date}",
            'badge_text': '🔜 Coming Soon'
        }
    elif release_status == 'distant_future':
        prefix = '~' if is_placeholder else ''
        return {
            'status_text': 'Distant Future',
            'status_emoji': '🔮',
            'status_color': 'blue',
            'date_text': f"{prefix}{release_date}",
            'badge_text': '🔮 Distant Future'
        }
    else:
        return {
            'status_text': 'Unknown',
            'status_emoji': '❓',
            'status_color': 'gray',
            'date_text': release_date,
            'badge_text': '❓ Unknown'
        }


def is_adult_content(game):
    """Check if a game contains adult content based on community tags."""
    adult_tags = {
        'sexual content', 'nudity', 'mature', 'nsfw', 'adult content', 
        'hentai', 'sexual', 'erotic', 'adult', 'sex', 'nude'
    }
    
    community_tags = game.get('CommunityTags', '')
    if not community_tags or not isinstance(community_tags, str):
        return False
    
    # Convert tags to lowercase for comparison
    tags_lower = community_tags.lower()
    
    # Check if any adult tags are present
    for adult_tag in adult_tags:
        if adult_tag in tags_lower:
            return True
    
    return False

def display_game_card(game):
    """Display a detailed game card with enhanced layout and interactive elements."""
    with st.container():
        st.markdown("---")
        
        # Get demo status
        demo_status = get_demo_status(game)
        
        # Main layout: Header image on left, content on right
        col1, col2 = st.columns([1, 2])
        
        with col1:
            # Header image
            create_header_image(game)
            
            # Quick action buttons
            if pd.notna(game.get('URL')):
                st.link_button("🔗 View on Steam", game['URL'], use_container_width=True)
            
            if pd.notna(game.get('FirstTrailerURL')):
                st.link_button("🎬 Watch Trailer", game['FirstTrailerURL'], use_container_width=True)
            
            if pd.notna(game.get('FirstScreenshotURL')):
                st.link_button("🖼️ View Screenshot", game['FirstScreenshotURL'], use_container_width=True)
            
            # Developer contact buttons
            if pd.notna(game.get('SupportEmail')) and str(game['SupportEmail']).strip():
                st.link_button("📧 Contact Developer", f"mailto:{game['SupportEmail']}", use_container_width=True)
            
            if pd.notna(game.get('SupportURL')) and str(game['SupportURL']).strip():
                st.link_button("🌐 Developer Support", game['SupportURL'], use_container_width=True)
        
        with col2:
            # Game title and basic info
            st.subheader(f"🎮 {game['Name']}")
            
            # Date added badge
            if pd.notna(game.get('DateAdded')):
                date_str = format_date_added(game['DateAdded'])
                st.markdown(f'<span class="date-added-badge">📅 Added: {date_str}</span>', unsafe_allow_html=True)
            
            # Demo indicator
            if demo_status['has_demo']:
                st.info("🎯 Demo Available")
            
            # Metrics row
            metric_col1, metric_col2 = st.columns(2)
            
            with metric_col1:
                if pd.notna(game.get('ReleaseDate')):
                    release_date = pd.to_datetime(game['ReleaseDate'], errors='coerce')
                    if pd.notna(release_date):
                        st.metric("Release Date", release_date.strftime('%Y-%m-%d'))
            
            with metric_col2:
                if demo_status['has_demo']:
                    st.success("🎯 Demo Available")
            
            # Horizontal row for optional info
            info_cols = st.columns(3)

            # Slot 1: Developer Email
            with info_cols[0]:
                if pd.notna(game.get('SupportEmail')) and str(game['SupportEmail']).strip():
                    st.link_button("📧 Dev Email", f"mailto:{game['SupportEmail']}")
            
            # Slot 2: Developer URL
            with info_cols[1]:
                if pd.notna(game.get('SupportURL')) and str(game['SupportURL']).strip():
                    st.link_button("🌐 Dev URL", game['SupportURL'])

            # Slot 3: Demo Status
            with info_cols[2]:
                demo_status = get_demo_status(game)
                if demo_status['has_demo']:
                    st.info("🎯 Demo")
            
            # Short description
            if pd.notna(game.get('ShortDescription')):
                st.write(game['ShortDescription'])
            
            # Display real Steam Community Tags
            display_game_tags(game)
            
            # Date information row (at the bottom)
            date_parts = []
            date_added = game.get('DateAdded')
            if date_added is not None:
                date_added_str = f"📅 Added: {format_date_added(date_added)}"
                date_parts.append(date_added_str)
            
            if date_parts:
                st.caption(" &nbsp; • &nbsp; ".join(date_parts), unsafe_allow_html=True)
            
            # Genres, categories, and developer info in compact format
            info_col1, info_col2 = st.columns(2)
            
            with info_col1:
                if pd.notna(game.get('Genres')):
                    st.write(f"**🎨 Genres:** {game['Genres']}")
                if pd.notna(game.get('Developers')) and str(game['Developers']).strip():
                    st.write(f"**👥 Developer:** {game['Developers']}")
            
            with info_col2:
                if pd.notna(game.get('Categories')):
                    st.write(f"**📋 Categories:** {game['Categories']}")
                if pd.notna(game.get('Publishers')) and str(game['Publishers']).strip():
                    st.write(f"**🏢 Publisher:** {game['Publishers']}")
            
            # Primary detailed description (removes duplication)
            primary_desc = get_primary_description(game)
            if primary_desc:
                with st.expander("📝 Detailed Description", expanded=False):
                    st.write(primary_desc)
            
            # Display real Steam Community Tags
            display_game_tags(game)
        
        # Enhanced media gallery (full width)
        if (pd.notna(game.get('Screenshots')) and str(game['Screenshots']).strip() and str(game['Screenshots']) != '[]') or \
           (pd.notna(game.get('Movies')) and str(game['Movies']).strip() and str(game['Movies']) != '[]'):
            with st.expander("🎮 Media Gallery", expanded=False):
                display_enhanced_media_gallery(game.get('Screenshots', ''), game.get('Movies', ''))

def main():
    st.title("🎮 Steam Game Tracker")
    st.write("Discover new Steam games with detailed descriptions, trailers, and screenshots!")
    
    # Initialize session state for infinite scroll and favorites
    if 'games_shown' not in st.session_state:
        st.session_state.games_shown = 20
    
    # Initialize favorites state
    init_favorites_state()
    
    # Load localStorage data
    get_localstorage_data()
    
    # Load data
    df = load_steam_data()
    
    if df.empty:
        st.warning("No data available. Please run the steam tracker script first.")
        st.stop()
    
    # Sidebar filters
    st.sidebar.header("🔍 Filters")
    
    # Favorites and Lists Management Section
    st.sidebar.markdown("---")
    st.sidebar.subheader("⭐ Favorites & Lists")
    
    # List filter dropdown
    list_filter_options = ["All", "Favorites"]
    if st.session_state.custom_lists:
        list_filter_options.extend(list(st.session_state.custom_lists.keys()))
    
    selected_list_filter = st.sidebar.selectbox(
        "📋 Show List",
        list_filter_options,
        help="Filter by favorites or custom lists"
    )
    
    # List management
    with st.sidebar.expander("📝 Manage Lists", expanded=False):
        st.write("**Create New List:**")
        new_list_name = st.text_input("List Name", key="new_list_input")
        if st.button("➕ Create List") and new_list_name.strip():
            if new_list_name not in st.session_state.custom_lists:
                st.session_state.custom_lists[new_list_name] = []
                save_to_localstorage()  # Save to localStorage
                st.success(f"Created list: {new_list_name}")
                st.rerun()
            else:
                st.warning("List already exists!")
        
        # Delete existing lists
        if st.session_state.custom_lists:
            st.write("**Delete Lists:**")
            for list_name in list(st.session_state.custom_lists.keys()):
                list_count = len(st.session_state.custom_lists[list_name])
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.write(f"{list_name} ({list_count} games)")
                with col2:
                    if st.button("🗑️", key=f"delete_{list_name}"):
                        del st.session_state.custom_lists[list_name]
                        save_to_localstorage()  # Save to localStorage
                        st.success(f"Deleted {list_name}")
                        st.rerun()
    
    # Display current favorites and lists stats
    if st.session_state.favorites:
        st.sidebar.write(f"⭐ **{len(st.session_state.favorites)} favorites**")
    
    if st.session_state.custom_lists:
        for list_name, games in st.session_state.custom_lists.items():
            st.sidebar.write(f"📋 **{list_name}**: {len(games)} games")
    
    st.sidebar.markdown("---")
    
    # Keyword search
    keyword_search = st.sidebar.text_input(
        "🔎 Search by Keywords",
        placeholder="e.g., rpg, strategy, multiplayer, souls-like",
        help="Enter keywords separated by commas. Will search in game name, descriptions, genres, and categories."
    )
    
    # Community Tags filter
    all_tags = set()
    if 'CommunityTags' in df.columns:
        for tags_str in df['CommunityTags'].dropna():
            if str(tags_str).strip():
                all_tags.update([t.strip() for t in str(tags_str).split(',') if t.strip()])
    
    selected_tags = st.sidebar.multiselect(
        "🎯 Include Tags (ALL required)",
        sorted(list(all_tags)),
        help="Games must have ALL selected tags"
    )
    
    excluded_tags = st.sidebar.multiselect(
        "🚫 Exclude Tags (ANY excludes)",
        sorted(list(all_tags)),
        help="Games with ANY of these tags will be excluded"
    )
    
    # Demo filter
    demo_filter = st.sidebar.selectbox(
        "🎯 Demo Availability",
        ["All", "Has Demo"],
        help="Filter by demo availability"
    )
    
    # Release status filter
    release_status_filter = st.sidebar.selectbox(
        "🚀 Release Status",
        ["All", "Released", "Coming Soon", "Distant Future"],
        help="Filter by release status"
    )
    
    # Adult content filter
    show_adult_content = st.sidebar.checkbox(
        "🔞 Show Adult Content",
        value=False,
        help="Show games with mature/adult content (hidden by default)"
    )
    
    # Sort options
    st.sidebar.subheader("🔄 Sort Options")
    sort_option = st.sidebar.selectbox(
        "Sort By",
        ["Date Added (Newest First)", "Date Added (Oldest First)", "Name (A-Z)", "Name (Z-A)", "Release Status"],
        help="Choose how to sort the results"
    )
    
    # Apply filters
    filtered_df: pd.DataFrame = df.copy()
    
    # Keyword filter
    if keyword_search:
        result = filter_games_by_keywords(filtered_df, keyword_search)
        if isinstance(result, pd.DataFrame):
            filtered_df = result
        else:
            filtered_df = pd.DataFrame(result)
    
    # Community tags filter - INCLUDE (AND logic: ALL tags must be present)
    if selected_tags and 'CommunityTags' in filtered_df.columns:
        include_mask = pd.Series([True] * len(filtered_df), index=filtered_df.index)
        for tag in selected_tags:
            tag_present_mask = filtered_df['CommunityTags'].astype(str).str.contains(tag, case=False, na=False, regex=False)
            include_mask = include_mask & tag_present_mask  # AND logic - all tags must be present
        filtered_df = filtered_df.loc[include_mask].copy()
    
    # Community tags filter - EXCLUDE (OR logic: ANY tag excludes the game)
    if excluded_tags and 'CommunityTags' in filtered_df.columns:
        exclude_mask = pd.Series([False] * len(filtered_df), index=filtered_df.index)
        for tag in excluded_tags:
            tag_present_mask = filtered_df['CommunityTags'].astype(str).str.contains(tag, case=False, na=False, regex=False)
            exclude_mask = exclude_mask | tag_present_mask  # OR logic - any tag excludes
        # Keep games that DON'T have excluded tags
        filtered_df = filtered_df.loc[~exclude_mask].copy()
    
    # Demo filter
    if demo_filter == "Has Demo":
        demo_mask = (filtered_df['Demo'] == True) | (filtered_df['IsDemo'] == True)
        filtered_df = filtered_df.loc[demo_mask].copy()
    
    # Release status filter
    if release_status_filter != "All" and 'ReleaseStatus' in filtered_df.columns:
        status_map = {
            "Released": "released",
            "Coming Soon": "coming_soon", 
            "Distant Future": "distant_future"
        }
        target_status = status_map.get(release_status_filter)
        if target_status:
            status_mask = filtered_df['ReleaseStatus'].astype(str).str.lower() == target_status
            filtered_df = filtered_df.loc[status_mask].copy()
    
    # Adult content filter (filter out by default unless explicitly enabled)
    if not show_adult_content:
        adult_mask = filtered_df.apply(lambda row: not is_adult_content(row), axis=1)
        filtered_df = filtered_df.loc[adult_mask].copy()
    
    # Favorites and Lists filter
    if selected_list_filter != "All":
        if selected_list_filter == "Favorites":
            filtered_df = filter_by_favorites_and_lists(filtered_df, "Favorites")
        else:
            # Custom list filter
            filtered_df = filter_by_favorites_and_lists(filtered_df, "Custom List", selected_list_filter)
    
    # Apply sorting
    if not filtered_df.empty:
        if sort_option == "Date Added (Newest First)":
            # Sort by DateAdded desc, then by Name asc
            filtered_df = filtered_df.sort_values(['DateAdded', 'Name'], ascending=[False, True], na_position='last')
        elif sort_option == "Date Added (Oldest First)":
            filtered_df = filtered_df.sort_values(['DateAdded', 'Name'], ascending=[True, True], na_position='first')
        elif sort_option == "Name (A-Z)":
            filtered_df = filtered_df.sort_values('Name', ascending=True)
        elif sort_option == "Name (Z-A)":
            filtered_df = filtered_df.sort_values('Name', ascending=False)
        elif sort_option == "Release Status":
            # Sort by release status (released first, then coming soon, then distant future)
            status_order = {'released': 0, 'coming_soon': 1, 'distant_future': 2, 'unknown': 3}
            if 'ReleaseStatus' in filtered_df.columns:
                filtered_df['_sort_order'] = filtered_df['ReleaseStatus'].apply(lambda x: status_order.get(x, 3))
                filtered_df = filtered_df.sort_values(by=['_sort_order', 'Name'], ascending=[True, True])
                filtered_df = filtered_df.drop('_sort_order', axis=1)
    
    # Display results
    st.header(f"📊 Results ({len(filtered_df)} games)")
    
    if filtered_df.empty:
        st.info("No games found matching your criteria. Try adjusting your filters.")
        return
    
    # --- "Infinite Scroll" Implementation ---
    # Slice dataframe for current view based on session state
    page_df = filtered_df.head(st.session_state.games_shown)
    
    st.write(f"Showing {len(page_df)} of {len(filtered_df)} games")
    
    # Display games in compact view
    for _, game in page_df.iterrows():
        with st.container():
            # 3-column layout optimized for medium resolutions: Image+Favorites | Game Data | Media
            img_col, data_col, media_col = st.columns([2.5, 3.5, 3], gap="medium")
            
            # Column 1: Main Image + Favorites/Lists
            with img_col:
                header_image = game.get('HeaderImage')
                if header_image is not None and str(header_image).strip():
                    st.image(str(header_image), use_container_width=True)
                else:
                    st.write("🎮")  # Fallback icon
                
                # Favorites and List Management (directly under image)
                fav_col, list_col = st.columns(2)
                
                with fav_col:
                    create_favorite_button(game)
                
                with list_col:
                    # Quick list management
                    game_id = get_game_id(game)
                    available_lists = list(st.session_state.custom_lists.keys())
                    
                    if available_lists:
                        with st.expander("📝 Lists", expanded=False):
                            create_list_management_buttons(game)
                    else:
                        st.caption("Create lists in sidebar")
                
                # Separator line under favorites/lists
                st.markdown("---")
            
            # Column 2: Game Data
            with data_col:
                # Game title
                game_url = game.get('URL', '#')
                if game_url is not None and str(game_url).strip():
                    st.write(f"**[{game['Name']}]({str(game_url)})**")
                else:
                    st.write(f"**{game['Name']}**")
                
                # Responsive 2x2 button layout for better medium-screen compatibility
                row1_cols = st.columns(2)
                row2_cols = st.columns(2)

                # Row 1: Developer Email and URL
                with row1_cols[0]:
                    support_email = game.get('SupportEmail')
                    if support_email is not None and str(support_email).strip():
                        st.link_button("📧 Dev Email", f"mailto:{str(support_email)}", use_container_width=True)
                
                with row1_cols[1]:
                    support_url = game.get('SupportURL')
                    if support_url is not None and str(support_url).strip():
                        st.link_button("🌐 Dev URL", str(support_url), use_container_width=True)

                # Row 2: Release Status and Demo Status
                with row2_cols[0]:
                    release_display = get_release_status_display(game)
                    st.markdown(
                        f"""<div style="
                            background-color: #d1ecf1; 
                            color: #0c5460; 
                            padding: 0.25rem 0.75rem; 
                            border-radius: 0.375rem; 
                            text-align: center; 
                            font-weight: 400; 
                            border: 1px solid #bee5eb;
                            height: 38px;
                            display: flex;
                            align-items: center;
                            justify-content: center;
                            font-size: 14px;
                            width: 100%;
                        ">{release_display['badge_text']}</div>""", 
                        unsafe_allow_html=True
                    )

                with row2_cols[1]:
                    demo_status = get_demo_status(game)
                    if demo_status['has_demo']:
                        st.markdown(
                            f"""<div style="
                                background-color: #d1ecf1; 
                                color: #0c5460; 
                                padding: 0.25rem 0.75rem; 
                                border-radius: 0.375rem; 
                                text-align: center; 
                                font-weight: 400; 
                                border: 1px solid #bee5eb;
                                height: 38px;
                                display: flex;
                                align-items: center;
                                justify-content: center;
                                font-size: 14px;
                                width: 100%;
                            ">🎯 Demo</div>""", 
                            unsafe_allow_html=True
                        )
                
                # Description
                short_desc = game.get('ShortDescription')
                if short_desc is not None and str(short_desc).strip():
                    st.write(str(short_desc))
                
                # Display real Steam Community Tags
                display_game_tags(game)
                
                # Date information row (at the bottom)
                date_parts = []
                date_added = game.get('DateAdded')
                if date_added is not None:
                    date_added_str = f"📅 Added: {format_date_added(date_added)}"
                    date_parts.append(date_added_str)
                
                # Add release date information
                release_display = get_release_status_display(game)
                if release_display['date_text'] != 'Unknown':
                    release_date_str = f"{release_display['status_emoji']} {release_display['date_text']}"
                    date_parts.append(release_date_str)
                
                # Add demo status
                demo_status = get_demo_status(game)
                if demo_status['has_demo']:
                    date_parts.append("🎯 Demo Available")
                
                if date_parts:
                    st.caption(" &nbsp; • &nbsp; ".join(date_parts), unsafe_allow_html=True)

            # Column 3: Media (Trailer + All Screenshots in Tabs)
            with media_col:
                # More balanced trailer/screenshot layout for medium resolutions
                trailer_col, screenshots_col = st.columns([1, 1])
                
                with trailer_col:
                    st.markdown("**🎬 Trailer**")
                    trailer_url = game.get('FirstTrailerURL')
                    if trailer_url is not None and str(trailer_url).strip():
                        trailer_url_str = str(trailer_url)
                        try:
                            st.video(trailer_url_str)
                        except:
                            st.markdown(f"**[🎬 Watch]({trailer_url_str})**")
                    else:
                        st.info("No trailer available")
                
                # Screenshots section with true horizontal scrollable layout
                with screenshots_col:
                    st.markdown("**📷 Screenshots**")
                    try:
                        screenshots_json = game.get('Screenshots', '')
                        if screenshots_json is not None and str(screenshots_json).strip() and str(screenshots_json) != '[]':
                            screenshots = json.loads(str(screenshots_json))
                            if screenshots:
                                # Create CSS for horizontal scrolling container (responsive)
                                st.markdown("""
                                <style>
                                .screenshot-container {
                                    display: flex;
                                    overflow-x: auto;
                                    gap: 8px;
                                    padding: 5px 0;
                                    height: 160px;
                                    align-items: center;
                                }
                                .screenshot-container img {
                                    height: 140px;
                                    width: auto;
                                    flex-shrink: 0;
                                    border-radius: 6px;
                                    object-fit: cover;
                                }
                                .screenshot-container::-webkit-scrollbar {
                                    height: 6px;
                                }
                                .screenshot-container::-webkit-scrollbar-track {
                                    background: #f1f1f1;
                                    border-radius: 3px;
                                }
                                .screenshot-container::-webkit-scrollbar-thumb {
                                    background: #888;
                                    border-radius: 3px;
                                }
                                .screenshot-container::-webkit-scrollbar-thumb:hover {
                                    background: #555;
                                }
                                @media (max-width: 1400px) {
                                    .screenshot-container {
                                        height: 140px;
                                    }
                                    .screenshot-container img {
                                        height: 120px;
                                    }
                                }
                                </style>
                                """, unsafe_allow_html=True)
                                
                                # Build HTML for horizontal scrolling screenshots
                                screenshots_html = '<div class="screenshot-container">'
                                for screenshot in screenshots:
                                    img_url = screenshot.get('thumbnail') or screenshot.get('full', '')
                                    if img_url:
                                        screenshots_html += f'<img src="{img_url}" alt="Screenshot">'
                                screenshots_html += '</div>'
                                
                                st.markdown(screenshots_html, unsafe_allow_html=True)
                            else:
                                st.info("No screenshots available")
                        else:
                            st.info("No screenshots available")
                    except (json.JSONDecodeError, TypeError, IndexError):
                        st.info("No screenshots available")
    
    # "Load More" button at the bottom of the page content
    if st.session_state.games_shown < len(filtered_df):
        # Center the button
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            if st.button("Load More Games", use_container_width=True):
                st.session_state.games_shown += 20
                st.rerun()

    # Summary statistics
    with st.sidebar:
        st.markdown("---")
        st.subheader("📈 Statistics")
        st.metric("Total Games", len(df))
        st.metric("Filtered Results", len(filtered_df))
        
        # Demo availability
        demo_count = len(filtered_df[(filtered_df['Demo'] == True) | (filtered_df['IsDemo'] == True)])
        st.write(f"**Games with Demo:** {demo_count}")
        
        # Release status breakdown
        if 'ReleaseStatus' in filtered_df.columns:
            st.write("**Release Status:**")
            released_count = len(filtered_df[filtered_df['ReleaseStatus'] == 'released'])
            coming_soon_count = len(filtered_df[filtered_df['ReleaseStatus'] == 'coming_soon'])
            distant_future_count = len(filtered_df[filtered_df['ReleaseStatus'] == 'distant_future'])
            
            st.write(f"- ✅ Released: {released_count}")
            st.write(f"- 🔜 Coming Soon: {coming_soon_count}")
            st.write(f"- 🔮 Distant Future: {distant_future_count}")
        
        # Tag filtering breakdown
        if selected_tags or excluded_tags:
            st.write("**🎯 Tag Filtering:**")
            if selected_tags:
                st.write(f"- ✅ Requires ALL: {', '.join(selected_tags[:3])}{' (+more)' if len(selected_tags) > 3 else ''}")
            if excluded_tags:
                st.write(f"- 🚫 Excludes ANY: {', '.join(excluded_tags[:3])}{' (+more)' if len(excluded_tags) > 3 else ''}")
        
        # Favorites and Lists breakdown
        if st.session_state.favorites or st.session_state.custom_lists:
            st.write("**⭐ Favorites & Lists:**")
            if st.session_state.favorites:
                st.write(f"- ⭐ Total Favorites: {len(st.session_state.favorites)}")
            if st.session_state.custom_lists:
                for list_name, games in st.session_state.custom_lists.items():
                    st.write(f"- 📋 {list_name}: {len(games)} games")
            if selected_list_filter != "All":
                st.write(f"- 🔍 Currently showing: {selected_list_filter}")
        
        # Update schedule
        st.markdown("---")
        croatia_time = get_croatian_time()
        
        if croatia_time.hour >= 20:
            hours_until_reset = 24 - croatia_time.hour + 20
        else:
            hours_until_reset = 20 - croatia_time.hour
        
        st.write(f"**⏰ Tracker updates in:** {hours_until_reset} hours")
        
        # Adult content breakdown
        if not df.empty:
            total_adult_games = len(df[df.apply(lambda row: is_adult_content(row), axis=1)])
            if not show_adult_content and total_adult_games > 0:
                st.write(f"**🔞 Adult Content:** {total_adult_games} games hidden")
            elif show_adult_content and total_adult_games > 0:
                visible_adult_games = len(filtered_df[filtered_df.apply(lambda row: is_adult_content(row), axis=1)])
                st.write(f"**🔞 Adult Content:** {visible_adult_games} games shown")

if __name__ == "__main__":
    main()
