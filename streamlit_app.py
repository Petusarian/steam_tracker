import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import json
import re
from datetime import datetime
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

# Custom CSS for better media display
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

@st.cache_data(ttl=300)
def load_steam_data():
    """Load Steam game data from Google Sheets with caching."""
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
            return df
        except gspread.WorksheetNotFound:
            st.error("Steam_Master worksheet not found. Please run the data script first.")
            return pd.DataFrame()
    except Exception as e:
        st.error(f"Error loading data: {e}")
        # Show more detailed error information
        import traceback
        st.error(f"Detailed error: {traceback.format_exc()}")
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
    
    # Initialize session state for infinite scroll
    if 'games_shown' not in st.session_state:
        st.session_state.games_shown = 20
    
    # Load data
    df = load_steam_data()
    
    if df.empty:
        st.warning("No data available. Please run the steam tracker script first.")
        st.stop()
    
    # Sidebar filters
    st.sidebar.header("🔍 Filters")
    
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
        "🎯 Community Tags",
        sorted(list(all_tags)),
        help="Select one or more community tags to filter by"
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
    
    # Community tags filter
    if selected_tags and 'CommunityTags' in filtered_df.columns:
        tag_mask = pd.Series([False] * len(filtered_df), index=filtered_df.index)
        for tag in selected_tags:
            mask = filtered_df['CommunityTags'].astype(str).str.contains(tag, case=False, na=False, regex=False)
            tag_mask = tag_mask | mask
        filtered_df = filtered_df.loc[tag_mask].copy()
    
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
                filtered_df = filtered_df.sort_values(['_sort_order', 'Name'], ascending=[True, True])
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
            # 3-column layout: Main Image | Game Data | Media (Trailer + Screenshots)
            img_col, data_col, media_col = st.columns([2, 2, 4], gap="medium")
            
            # Column 1: Main Image
            with img_col:
                header_image = game.get('HeaderImage')
                if header_image is not None and str(header_image).strip():
                    st.image(str(header_image), use_container_width=True)
                else:
                    st.write("🎮")  # Fallback icon
            
            # Column 2: Game Data
            with data_col:
                # Game title
                game_url = game.get('URL', '#')
                if game_url is not None and str(game_url).strip():
                    st.write(f"**[{game['Name']}]({str(game_url)})**")
                else:
                    st.write(f"**{game['Name']}**")
                
                # Horizontal row for optional info
                info_cols = st.columns(4)

                # Slot 1: Developer Email
                with info_cols[0]:
                    support_email = game.get('SupportEmail')
                    if support_email is not None and str(support_email).strip():
                        st.link_button("📧 Dev Email", f"mailto:{str(support_email)}")
                
                # Slot 2: Developer URL
                with info_cols[1]:
                    support_url = game.get('SupportURL')
                    if support_url is not None and str(support_url).strip():
                        st.link_button("🌐 Dev URL", str(support_url))

                # Slot 3: Release Status
                with info_cols[2]:
                    release_display = get_release_status_display(game)
                    st.button(release_display['badge_text'], disabled=True, use_container_width=True)

                # Slot 4: Demo Status
                with info_cols[3]:
                    demo_status = get_demo_status(game)
                    if demo_status['has_demo']:
                        st.button("🎯 Demo", disabled=True, use_container_width=True)
                
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
                # Trailer section (always visible first)
                trailer_col, screenshots_col = st.columns([1, 2])
                
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
                                # Create CSS for horizontal scrolling container
                                st.markdown("""
                                <style>
                                .screenshot-container {
                                    display: flex;
                                    overflow-x: auto;
                                    gap: 10px;
                                    padding: 5px 0;
                                    height: 200px;
                                    align-items: center;
                                }
                                .screenshot-container img {
                                    height: 180px;
                                    width: auto;
                                    flex-shrink: 0;
                                    border-radius: 8px;
                                    object-fit: cover;
                                }
                                .screenshot-container::-webkit-scrollbar {
                                    height: 8px;
                                }
                                .screenshot-container::-webkit-scrollbar-track {
                                    background: #f1f1f1;
                                    border-radius: 4px;
                                }
                                .screenshot-container::-webkit-scrollbar-thumb {
                                    background: #888;
                                    border-radius: 4px;
                                }
                                .screenshot-container::-webkit-scrollbar-thumb:hover {
                                    background: #555;
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
                
                st.markdown("---")
    
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

if __name__ == "__main__":
    main()
