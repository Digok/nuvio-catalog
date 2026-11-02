#!/usr/bin/env python3
"""Build a static Stremio/Nuvio catalog addon (metadata only, no streams).

Sources: IMDb non-commercial datasets (ratings/basics), Cinemeta (trending),
Wikidata (Oscar, MCU, anime, Brazilian films). Output: site/ (GitHub Pages).
"""
import csv, gzip, json, math, os, shutil, sys, time, urllib.parse, urllib.request
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
SITE = os.path.join(ROOT, "docs")
UA = {"User-Agent": "nuvio-catalog/1.0 (personal, non-commercial)"}
N = 100  # titles per row

csv.field_size_limit(10**7)


def get_json(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={**UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except Exception as e:
            if i == tries - 1:
                print("WARN fetch failed", url, e, file=sys.stderr)
                return None
            time.sleep(3)


def download_imdb():
    os.makedirs(DATA, exist_ok=True)
    for name in ("title.basics.tsv.gz", "title.ratings.tsv.gz"):
        path = os.path.join(DATA, name)
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < 3 * 86400:
            continue
        req = urllib.request.Request("https://datasets.imdbws.com/" + name, headers=UA)
        with urllib.request.urlopen(req, timeout=600) as r, open(path + ".part", "wb") as f:
            shutil.copyfileobj(r, f)
        os.replace(path + ".part", path)


def tsv(name):
    with gzip.open(os.path.join(DATA, name), "rt", encoding="utf-8") as f:
        yield from csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE)


def load_titles():
    ratings = {}
    for r in tsv("title.ratings.tsv.gz"):
        v = int(r["numVotes"])
        if v >= 500:
            ratings[r["tconst"]] = (float(r["averageRating"]), v)
    titles = {}
    keep = {"movie": "movie", "tvMovie": "movie", "tvSeries": "series", "tvMiniSeries": "series"}
    for r in tsv("title.basics.tsv.gz"):
        t = r["tconst"]
        if t not in ratings or r["titleType"] not in keep or r["isAdult"] == "1":
            continue
        year = int(r["startYear"]) if r["startYear"].isdigit() else None
        rating, votes = ratings[t]
        titles[t] = {
            "id": t, "kind": r["titleType"], "type": keep[r["titleType"]],
            "name": r["primaryTitle"], "orig": r["originalTitle"], "year": year,
            "end": int(r["endYear"]) if r["endYear"].isdigit() else None,
            "genres": [] if r["genres"] == "\\N" else r["genres"].split(","),
            "rating": rating, "votes": votes,
        }
    return titles


def weighted(items, m):
    """IMDb-style Bayesian rating: rewards titles that are both loved and widely seen."""
    if not items:
        return []
    c = sum(i["rating"] for i in items) / len(items)
    return sorted(items, key=lambda i: (i["votes"] / (i["votes"] + m)) * i["rating"] + (m / (i["votes"] + m)) * c, reverse=True)


def meta(t):
    year = t["year"]
    info = str(year) if year else ""
    if t["type"] == "series" and year:
        info = f"{year}-{t['end'] or ''}"
    return {
        "id": t["id"], "type": t["type"], "name": t["name"],
        "poster": f"https://images.metahub.space/poster/medium/{t['id']}/img",
        "background": f"https://images.metahub.space/background/medium/{t['id']}/img",
        "releaseInfo": info, "imdbRating": f"{t['rating']:.1f}", "genres": t["genres"],
    }


PREFIXES = "PREFIX wdt: <http://www.wikidata.org/prop/direct/> PREFIX wd: <http://www.wikidata.org/entity/> "


def wikidata_ids(key, query):
    """QLever mirror first (fast, no rate limit), then Wikidata (rate-limited), then last good cache."""
    cache = os.path.join(DATA, f"wikidata_{key}.json")
    q = urllib.parse.urlencode({"query": PREFIXES + query, "format": "json"})
    res = get_json("https://qlever.dev/api/wikidata?" + q) or get_json("https://query.wikidata.org/sparql?" + q, tries=1)
    if res:
        ids = sorted({b["imdb"]["value"] for b in res["results"]["bindings"] if b["imdb"]["value"].startswith("tt")})
        if ids:
            with open(cache, "w") as f:
                json.dump(ids, f)
            return set(ids)
    if os.path.exists(cache):
        print("WARN using cached", key, file=sys.stderr)
        return set(json.load(open(cache)))
    print("WARN no data for", key, file=sys.stderr)
    return set()


def cinemeta(path, pages=2):
    ids = []
    for p in range(pages):
        suffix = f"/skip={p * 50}" if p else ""
        res = get_json(f"https://cinemeta-catalogs.strem.io/{path}{suffix}.json")
        if not res:
            break
        ids += [m.get("imdb_id") or m.get("id") for m in res.get("metas", [])]
    return [i for i in ids if i]


def resolve(titles, wanted, by_name):
    """Curated (title, year, type) -> IMDb id, picking the most-voted exact match."""
    out, missing = [], []
    for name, year, typ in wanted:
        cands = [t for t in by_name.get(name.lower(), []) if t["type"] == typ and t["year"] and abs(t["year"] - year) <= 1]
        if cands:
            out.append(max(cands, key=lambda t: t["votes"]))
        else:
            missing.append(f"{name} ({year})")
    return out, missing


FINANCE_MOVIES = [
    ("The Big Short", 2015), ("Margin Call", 2011), ("The Wolf of Wall Street", 2013), ("Wall Street", 1987),
    ("Wall Street: Money Never Sleeps", 2010), ("Too Big to Fail", 2011), ("Inside Job", 2010), ("Boiler Room", 2000),
    ("Trading Places", 1983), ("Rogue Trader", 1999), ("Dumb Money", 2023), ("Enron: The Smartest Guys in the Room", 2005),
    ("Becoming Warren Buffett", 2017), ("Barbarians at the Gate", 1993), ("Glengarry Glen Ross", 1992), ("Arbitrage", 2012),
    ("Equity", 2016), ("Moneyball", 2011), ("The Founder", 2016), ("Owning Mahowny", 2003), ("Other People's Money", 1991),
    ("The Laundromat", 2019), ("Money Monster", 2016), ("Pain Hustlers", 2023), ("The Wizard of Lies", 2017),
    ("Molly's Game", 2017), ("The Pursuit of Happyness", 2006), ("Catch Me If You Can", 2002), ("The Social Network", 2010),
    ("Gold", 2016), ("Capitalism: A Love Story", 2009), ("The International", 2009),
]
FINANCE_SERIES = [
    ("Billions", 2016), ("Industry", 2020), ("Succession", 2018), ("Black Monday", 2019), ("Dirty Money", 2018),
    ("Madoff: The Monster of Wall Street", 2023), ("Super Pumped", 2022), ("The Dropout", 2022), ("WeCrashed", 2022),
    ("Ozark", 2017), ("Halt and Catch Fire", 2014), ("Silicon Valley", 2014),
]
MARVEL_SERIES = [
    ("Agents of S.H.I.E.L.D.", 2013), ("Agent Carter", 2015), ("Daredevil", 2015), ("Jessica Jones", 2015),
    ("Luke Cage", 2016), ("Iron Fist", 2017), ("The Defenders", 2017), ("The Punisher", 2017), ("WandaVision", 2021),
    ("The Falcon and the Winter Soldier", 2021), ("Loki", 2021), ("What If...?", 2021), ("Hawkeye", 2021),
    ("Moon Knight", 2022), ("Ms. Marvel", 2022), ("She-Hulk: Attorney at Law", 2022), ("Secret Invasion", 2023),
    ("Echo", 2024), ("Agatha All Along", 2024), ("X-Men '97", 2024), ("Daredevil: Born Again", 2025), ("Ironheart", 2025),
    ("Your Friendly Neighborhood Spider-Man", 2025),
]
MEDICAL_SERIES = [
    ("Grey's Anatomy", 2005), ("House", 2004), ("House M.D.", 2004), ("ER", 1994), ("Scrubs", 2001), ("The Pitt", 2025),
    ("The Good Doctor", 2017), ("New Amsterdam", 2018), ("The Resident", 2018), ("Chicago Med", 2015), ("The Knick", 2014),
    ("This Is Going to Hurt", 2022), ("Call the Midwife", 2012), ("Code Black", 2015), ("Nip/Tuck", 2003),
    ("Private Practice", 2007), ("M*A*S*H", 1972), ("St. Elsewhere", 1982), ("Doogie Howser, M.D.", 1989),
    ("Sob Pressão", 2017), ("Pure Genius", 2016), ("The Night Shift", 2014), ("Hospital Playlist", 2020),
]

MOVIE_GENRES = ["Action", "Adventure", "Animation", "Comedy", "Crime", "Documentary", "Drama", "Family", "Fantasy",
                "Horror", "Mystery", "Romance", "Sci-Fi", "Thriller", "War", "Western", "Biography", "History", "Sport", "Musical"]
SERIES_GENRES = ["Drama", "Comedy", "Crime", "Sci-Fi", "Animation", "Documentary", "Thriller", "Action", "Fantasy", "Mystery"]


def build():
    download_imdb()
    titles = load_titles()
    print("titles loaded:", len(titles))
    by_name = defaultdict(list)
    for t in titles.values():
        by_name[t["name"].lower()].append(t)
        if t["orig"].lower() != t["name"].lower():
            by_name[t["orig"].lower()].append(t)

    movies = [t for t in titles.values() if t["kind"] == "movie"]
    series = [t for t in titles.values() if t["type"] == "series"]
    rows, report = [], {}

    def add(cid, name, typ, items):
        seen, metas = set(), []
        for t in items:
            if t and t["id"] not in seen and t["type"] == typ:
                seen.add(t["id"])
                metas.append(meta(t))
            if len(metas) >= N:
                break
        if metas:
            rows.append((cid, name, typ, metas))
        report[cid] = len(metas)

    # Trending (Cinemeta popularity)
    add("trending_movies", "Trending Movies", "movie", [titles.get(i) for i in cinemeta("top/catalog/movie/top")])
    add("trending_series", "Trending Series", "series", [titles.get(i) for i in cinemeta("top/catalog/series/top")])
    this_year = time.gmtime().tm_year
    recent = [t for t in movies if t["year"] and t["year"] >= this_year - 1 and t["votes"] >= 5000]
    add("new_movies", "New & Noteworthy Movies", "movie", sorted(recent, key=lambda t: t["votes"], reverse=True))

    # All-time greats
    add("top_movies", "Top Rated Movies of All Time", "movie", weighted([t for t in movies if t["votes"] >= 100000], 100000))
    add("top_series", "Top Rated Series of All Time", "series", weighted([t for t in series if t["votes"] >= 50000], 50000))
    add("most_watched", "Most Popular Movies Ever", "movie", sorted(movies, key=lambda t: t["votes"], reverse=True))

    # By genre
    for g in MOVIE_GENRES:
        pool = [t for t in movies if g in t["genres"] and t["votes"] >= 25000]
        add(f"movies_{g.lower().replace('-', '')}", f"Best {g} Movies", "movie", weighted(pool, 25000))
    for g in SERIES_GENRES:
        pool = [t for t in series if g in t["genres"] and t["votes"] >= 10000]
        add(f"series_{g.lower().replace('-', '')}", f"Best {g} Series", "series", weighted(pool, 10000))

    # By decade
    for d in range(1960, (this_year // 10) * 10 + 1, 10):
        pool = [t for t in movies if t["year"] and d <= t["year"] < d + 10 and t["votes"] >= 25000]
        add(f"movies_{d}s", f"Best Movies of the {d}s", "movie", weighted(pool, 25000))

    # Wikidata lists
    oscar = wikidata_ids("oscar", "SELECT ?imdb WHERE { ?x wdt:P166 wd:Q102427 ; wdt:P345 ?imdb }")
    add("oscar_best_picture", "Oscar Best Picture Winners", "movie",
        sorted([titles[i] for i in oscar if i in titles], key=lambda t: t["year"] or 0, reverse=True))
    mcu = wikidata_ids("mcu", "SELECT ?imdb WHERE { ?x wdt:P1080|wdt:P179 wd:Q642878 ; wdt:P345 ?imdb }")
    mcu_t = [titles[i] for i in mcu if i in titles]
    add("mcu_movies", "Marvel Cinematic Universe (in release order)", "movie", sorted(mcu_t, key=lambda t: t["year"] or 0))
    marvel_s, missing_marvel = resolve(titles, [(n, y, "series") for n, y in MARVEL_SERIES], by_name)
    add("mcu_series", "Marvel Series", "series", sorted({t["id"]: t for t in mcu_t + marvel_s if t["type"] == "series"}.values(), key=lambda t: t["year"] or 0))
    anime = wikidata_ids("anime", "SELECT ?imdb WHERE { VALUES ?c {wd:Q63952888 wd:Q20650540 wd:Q1107} ?x wdt:P31 ?c ; wdt:P345 ?imdb }")
    anime_t = [titles[i] for i in anime if i in titles]
    add("anime_series", "Best Anime Series", "series", weighted([t for t in anime_t if t["votes"] >= 5000], 5000))
    add("anime_movies", "Best Anime Movies", "movie", weighted([t for t in anime_t if t["votes"] >= 5000], 5000))
    brazil = wikidata_ids("brazil", "SELECT ?imdb WHERE { VALUES ?c {wd:Q11424 wd:Q5398426} ?x wdt:P31 ?c ; wdt:P495 wd:Q155 ; wdt:P345 ?imdb }")
    br_t = [titles[i] for i in brazil if i in titles and titles[i]["votes"] >= 2000]
    add("brazil_movies", "Best Brazilian Movies", "movie", weighted(br_t, 5000))
    add("brazil_series", "Best Brazilian Series", "series", weighted(br_t, 2000))

    # Curated lists
    missing = {"mcu_series": missing_marvel}
    fin_m, missing["finance_movies"] = resolve(titles, [(n, y, "movie") for n, y in FINANCE_MOVIES], by_name)
    add("finance_movies", "Money, Markets & Wall Street", "movie", sorted(fin_m, key=lambda t: t["votes"], reverse=True))
    fin_s, missing["finance_series"] = resolve(titles, [(n, y, "series") for n, y in FINANCE_SERIES], by_name)
    add("finance_series", "Money & Business Series", "series", sorted(fin_s, key=lambda t: t["votes"], reverse=True))
    med, missing["medical_series"] = resolve(titles, [(n, y, "series") for n, y in MEDICAL_SERIES], by_name)
    add("medical_series", "Medical Dramas", "series", sorted(med, key=lambda t: t["votes"], reverse=True))

    write_site(rows)
    return report, {k: v for k, v in missing.items() if v}


def write_site(rows):
    tmp = SITE + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    for cid, name, typ, metas in rows:
        os.makedirs(os.path.join(tmp, "catalog", typ), exist_ok=True)
        with open(os.path.join(tmp, "catalog", typ, cid + ".json"), "w", encoding="utf-8") as f:
            json.dump({"metas": metas}, f, ensure_ascii=False, separators=(",", ":"))
    manifest = {
        "id": "com.digok.nuvio.catalog",
        "version": time.strftime("1.%Y%m%d.%H%M", time.gmtime()),
        "name": "Rodrigo's Catalog",
        "description": "Trending, all-time greats by genre and decade, Oscar winners, Marvel, anime, Brazilian cinema, finance and medical dramas. Metadata only (IMDb, Cinemeta, Wikidata).",
        "resources": ["catalog"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt"],
        "catalogs": [{"type": typ, "id": cid, "name": name} for cid, name, typ, _ in rows],
        "behaviorHints": {"configurable": False, "configurationRequired": False},
    }
    with open(os.path.join(tmp, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    open(os.path.join(tmp, ".nojekyll"), "w").close()
    git = os.path.join(SITE, ".git")
    if os.path.isdir(git):
        shutil.move(git, os.path.join(tmp, ".git"))
    shutil.rmtree(SITE, ignore_errors=True)
    os.replace(tmp, SITE)


if __name__ == "__main__":
    report, missing = build()
    for k, v in report.items():
        print(f"{v:4d}  {k}")
    print("rows:", sum(1 for v in report.values() if v), "titles:", sum(report.values()))
    if missing:
        print("UNRESOLVED:", json.dumps(missing, ensure_ascii=False))
