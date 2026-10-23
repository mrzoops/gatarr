import requests

def fetch_trakt_watchlist(username: str, client_id: str, media_type: str = "movies"):
    # media_type can be 'movies' or 'shows'
    url = f"https://api.trakt.tv/users/{username}/watchlist/{media_type}"
    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": client_id
    }
    try:
        res = requests.get(url, headers=headers, timeout=10)
        return res.json() if res.status_code == 200 else []
    except Exception as e:
        print(f"[Trakt Error] Failed to fetch {media_type}: {e}")
        return []

def check_destined_for_streaming(tmdb_id: int, media_type: str, tmdb_api_key: str, subscribed_services: list):
    """
    Checks if an unreleased or upcoming movie/show is an original production 
    destined directly for one of the user's subscribed streaming services.
    """
    endpoint = "movie" if media_type == "movie" else "tv"
    url = f"https://api.themoviedb.org/3/{endpoint}/{tmdb_id}?api_key={tmdb_api_key}&append_to_response=release_dates"
    
    try:
        data = requests.get(url, timeout=6).json()
    except Exception:
        return False, None

    active_pool = [s.lower() for s in (subscribed_services or [])]

    # For TV Series: Network check is reliable (Apple TV+, Netflix, Disney+, Max)
    if media_type == "show":
        alias_map = {
            "apple tv+": "apple tv+",
            "apple tv plus": "apple tv+",
            "netflix": "netflix",
            "disney+": "disney+",
            "disney plus": "disney+",
            "amazon prime video": "amazon",
            "prime video": "amazon",
            "max": "max",
            "hbo": "hbo",
            "hulu": "hulu",
            "paramount+": "paramount+",
            "paramount plus": "paramount+",
            "peacock": "peacock"
        }
        networks = [n.get("name", "").lower() for n in data.get("networks", [])]
        for sub in active_pool:
            target_kw = alias_map.get(sub, sub)
            for net in networks:
                if target_kw in net:
                    return True, f"Upcoming series release on your subscription ({net.title()})"

    # For Movies: Check if there is a theatrical release window first
    if media_type == "movie":
        release_dates_data = data.get("release_dates", {}).get("results", [])
        for country in release_dates_data:
            if country.get("iso_3166_1") == "US":
                for rel in country.get("release_dates", []):
                    # Type 3 is Theatrical. If it gets a theater run, it is NOT an instant streaming release.
                    if rel.get("type") == 3:
                        return False, None

        # Only check studios that exclusively produce direct-to-streaming movies
        exclusive_streaming_studios = {
            "netflix": "Netflix",
            "apple original films": "Apple TV+",
            "apple studios": "Apple TV+"
        }
        
        companies = [c.get("name", "").lower() for c in data.get("production_companies", [])]
        for sub in active_pool:
            for studio_key, service_name in exclusive_streaming_studios.items():
                if (service_name.lower() in sub or sub in service_name.lower()):
                    for comp in companies:
                        if studio_key in comp:
                            return True, f"Upcoming original streaming movie ({comp.title()})"

    return False, None

def evaluate_streaming(tmdb_id: int, media_type: str, tmdb_api_key: str, mode: str, subscribed_services: list = None):
    # First: evaluate if it's destined for a subscribed service
    is_destined, dest_reason = check_destined_for_streaming(tmdb_id, media_type, tmdb_api_key, subscribed_services)
    if is_destined:
        return True, dest_reason

    # Second: evaluate current live streaming providers via JustWatch
    endpoint = "movie" if media_type in ["movie", "movies"] else "tv"
    url = f"https://api.themoviedb.org/3/{endpoint}/{tmdb_id}/watch/providers?api_key={tmdb_api_key}"
    
    try:
        res = requests.get(url, timeout=6).json()
    except Exception as e:
        return False, f"Provider API lookup failed: {e}"

    us_providers = res.get("results", {}).get("US", {})
    flatrate = [p.get("provider_name") for p in us_providers.get("flatrate", [])]
    free = [p.get("provider_name") for p in (us_providers.get("free", []) + us_providers.get("ads", []))]
    rent = [p.get("provider_name") for p in us_providers.get("rent", [])]
    buy = [p.get("provider_name") for p in us_providers.get("buy", [])]
    rent_or_buy = list(set(rent + buy))

    if mode == "A":
        if flatrate or free:
            return True, f"Streaming on: {', '.join(flatrate + free)}"
        if rent_or_buy:
            return False, f"Only available to rent/buy ({', '.join(rent_or_buy[:3])})"
        return False, "Not streaming / Theatrical / Unreleased"

    elif mode in ["B", "C"]:
        active_pool = set(subscribed_services or [])
        matched = [p for p in flatrate if p in active_pool]

        if mode == "C" and "Free / Ad-Supported (Tubi, Pluto, Freevee)" in active_pool and free:
            matched += free

        if matched:
            return True, f"Included in your subscription: {', '.join(matched)}"
        if rent_or_buy:
            return False, f"Not in your subscriptions. Rent only on: {', '.join(rent_or_buy[:3])}"
        if flatrate:
            return False, f"Available on other services ({', '.join(flatrate[:3])}), not yours"
            
        return False, "Not streaming / Theatrical / Unreleased"

    return False, "Unknown mode"