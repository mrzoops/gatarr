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
    destined for one of the user's subscribed streaming services.
    """
    endpoint = "movie" if media_type == "movie" else "tv"
    url = f"https://api.themoviedb.org/3/{endpoint}/{tmdb_id}?api_key={tmdb_api_key}"
    
    try:
        data = requests.get(url, timeout=6).json()
    except Exception:
        return False, None

    active_pool = [s.lower() for s in (subscribed_services or [])]

    # Service name normalization aliases
    alias_map = {
        "apple tv+": "apple",
        "apple tv plus": "apple",
        "netflix": "netflix",
        "disney+": "disney",
        "disney plus": "disney",
        "amazon prime video": "amazon",
        "prime video": "amazon",
        "max": "max",
        "hbo": "max",
        "hulu": "hulu",
        "paramount+": "paramount",
        "paramount plus": "paramount",
        "peacock": "peacock"
    }

    user_keywords = set()
    for s in active_pool:
        for alias, kw in alias_map.items():
            if alias in s:
                user_keywords.add(kw)

    # 1. TV Series: evaluate 'networks'
    if media_type == "show":
        networks = [n.get("name", "") for n in data.get("networks", [])]
        for net in networks:
            net_lower = net.lower()
            for kw in user_keywords:
                if kw in net_lower:
                    return True, f"Upcoming release on your subscription ({net})"

    # 2. Movies & Shows: evaluate 'production_companies'
    companies = [c.get("name", "") for c in data.get("production_companies", [])]
    for comp in companies:
        comp_lower = comp.lower()
        for kw in user_keywords:
            if kw in comp_lower:
                return True, f"Upcoming original on your subscription ({comp})"

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