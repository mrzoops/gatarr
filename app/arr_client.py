import requests

class ArrClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {"X-Api-Key": self.api_key, "Content-Type": "application/json"}

    def test_connection(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/api/v3/system/status", headers=self.headers, timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def get_quality_profiles(self):
        try:
            r = requests.get(f"{self.base_url}/api/v3/qualityprofile", headers=self.headers, timeout=5)
            return r.json() if r.status_code == 200 else []
        except Exception:
            return []

    def get_root_folders(self):
        try:
            r = requests.get(f"{self.base_url}/api/v3/rootfolder", headers=self.headers, timeout=5)
            return r.json() if r.status_code == 200 else []
        except Exception:
            return []

    def get_movie(self, tmdb_id: int):
        try:
            r = requests.get(f"{self.base_url}/api/v3/movie?tmdbId={tmdb_id}", headers=self.headers, timeout=5)
            if r.status_code == 200:
                movies = r.json()
                return movies[0] if len(movies) > 0 else None
            return None
        except Exception:
            return None

    def get_series(self, tvdb_id: int):
        try:
            r = requests.get(f"{self.base_url}/api/v3/series?tvdbId={tvdb_id}", headers=self.headers, timeout=5)
            if r.status_code == 200:
                shows = r.json()
                return shows[0] if len(shows) > 0 else None
            return None
        except Exception:
            return None

    def movie_exists(self, tmdb_id: int) -> bool:
        return self.get_movie(tmdb_id) is not None

    def series_exists(self, tvdb_id: int) -> bool:
        return self.get_series(tvdb_id) is not None

    def add_movie(self, tmdb_id: int, title: str, year: int, profile_id: int, root_path: str):
        if self.movie_exists(tmdb_id):
            return True, "Already in Radarr"

        payload = {
            "title": title,
            "year": year,
            "tmdbId": int(tmdb_id),
            "qualityProfileId": int(profile_id),
            "rootFolderPath": root_path,
            "monitored": True,
            "addOptions": {"searchForMovie": True}
        }
        try:
            r = requests.post(f"{self.base_url}/api/v3/movie", json=payload, headers=self.headers, timeout=10)
            if r.status_code in [200, 201]:
                return True, "Approved & Added"
            elif r.status_code == 400 and "exists" in r.text.lower():
                return True, "Already in Radarr"
            return False, f"Radarr error: {r.status_code}"
        except Exception as e:
            return False, f"Connection error: {e}"

    def add_series(self, tvdb_id: int, title: str, year: int, profile_id: int, root_path: str):
        if self.series_exists(tvdb_id):
            return True, "Already in Sonarr"

        payload = {
            "title": title,
            "year": year,
            "tvdbId": int(tvdb_id),
            "qualityProfileId": int(profile_id),
            "rootFolderPath": root_path,
            "monitored": True,
            "addOptions": {"searchForMissingEpisodes": True}
        }
        try:
            r = requests.post(f"{self.base_url}/api/v3/series", json=payload, headers=self.headers, timeout=10)
            if r.status_code in [200, 201]:
                return True, "Approved & Added"
            elif r.status_code == 400 and "exists" in r.text.lower():
                return True, "Already in Sonarr"
            return False, f"Sonarr error: {r.status_code}"
        except Exception as e:
            return False, f"Connection error: {e}"

    def delete_movie(self, tmdb_id: int) -> bool:
        movie = self.get_movie(tmdb_id)
        if not movie:
            return True
        internal_id = movie.get("id")
        try:
            r = requests.delete(f"{self.base_url}/api/v3/movie/{internal_id}?deleteFiles=true", headers=self.headers, timeout=10)
            return r.status_code == 200
        except Exception:
            return False

    def delete_series(self, tvdb_id: int) -> bool:
        series = self.get_series(tvdb_id)
        if not series:
            return True
        internal_id = series.get("id")
        try:
            r = requests.delete(f"{self.base_url}/api/v3/series/{internal_id}?deleteFiles=true", headers=self.headers, timeout=10)
            return r.status_code == 200
        except Exception:
            return False