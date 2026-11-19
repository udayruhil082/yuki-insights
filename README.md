# Yuki Insights

Competitor intelligence software: competitor PR, campaigns and marketing moves for Fiama
(body wash, soap, body scrub, India).

**Watching:** Dove, Nivea, Pears, Palmolive, Chemist at Play, Love Beauty and Planet,
Lux, Santoor, Mamaearth, Ghar Soaps, with Fiama as the benchmark.

## Start the app on your laptop
1. Install Python once from https://www.python.org/downloads/ (on Windows, tick "Add Python to PATH").
2. Double-click **Start Yuki Insights** (`.bat` on Windows, `.command` on Mac).
3. The app opens in your browser at http://127.0.0.1:8765. Keep the small black window open while you use it.
4. Press **Refresh everything** any time. It also refreshes by itself when you open it, if the data is more than 6 hours old.

Everything stays on your computer. Nothing is published online.

## Online website (GitHub Pages)
This project is **public**, so anyone can find and open the website and see the code and research.
Fiama's own campaigns, launches and ideas are **never uploaded**: they stay in your browser or on your laptop
(`docs/data/fiama_activity.json` is listed in `.gitignore`). Posts saved with **Log a post** become public GitHub issues,
so only log public competitor posts.

- Website: Settings → Pages → Deploy from a branch → `main`, folder `/docs`.
- Every day at 8:00 AM India time GitHub runs the collector and updates `docs/data/`.
- To hide everything again: Settings → General → Danger zone → Change visibility → Private.
  The files stay and can still be edited, but the free plan turns the website off while the project is private.

## Laptop app with GitHub (optional)
1. Start the app on your laptop (above), open **Sources & help** and use **Connect to your GitHub**
   (a fine-grained key with Actions, Contents and Issues set to Read and write, for this repository only).
2. The app then downloads the latest data each time you open it, and **Refresh everything** asks GitHub to run now.
3. To change brands or search words, edit `config/brands.json` directly on GitHub.

The key is stored only on your laptop in `config/github_local.json`, which is never uploaded.
Fiama activity is copied to GitHub only if you add `"share_fiama": true` to that file, which you should do only
if the project is private.

## How it works
1. When you press Refresh (or open the app), it runs `radar/collect.py` (Python). If the optional GitHub setup is used, this also runs every day at 8:00 AM India time.
2. It searches general news, Indian marketing press and each brand's official / parent-company site
   (via Google News), plus each brand's YouTube feed. It keeps items that name the brand,
   and labels them: Launch, Campaign, Influencer, Celebrity, Sustainability,
   Pricing & Promo, Business, Partnership, Purpose.
3. Results are saved in `docs/data/` and shown on the dashboard (`docs/index.html`,
   published with GitHub Pages).
4. It also checks each brand's product range: prices, sizes and stock from the brand's own
   online store (Mamaearth, Chemist at Play, Ghar Soaps), and new or removed product pages
   from the other brands' websites. Changes show up in the feed and the "Products & prices" tab.
5. Every Monday a weekly digest is posted as a GitHub issue, and GitHub emails it to you.

## Everyday use
- **Log an Instagram post or creator:** click "Log a post" on the dashboard and fill in the form.
- **Refresh now (one click):** press "Refresh now" on the dashboard. The first time, it asks for a
  GitHub key that can only start the refresh (step-by-step help is on screen). It finishes in
  about 3 to 5 minutes and the page reloads itself. You can also go to Actions, open
  "Daily competitor check" and click "Run workflow".
- **Add or change a brand:** edit `config/brands.json`.
- **Update the research data:** edit `seed/Fiama_Competitor_Research.xlsx`, then run
  `python radar/import_excel.py`.
- **Download everything:** the Excel button on the dashboard.

## Files
| Path | What it is |
|---|---|
| `config/brands.json` | Brands, search words, YouTube and Instagram handles |
| `radar/collect.py` | The daily collector |
| `radar/products.py` | The product range and price checker |
| `radar/import_excel.py` | Turns the research Excel into dashboard data |
| `docs/index.html` | The dashboard page |
| `docs/data/` | Data the collector writes (news, stats, digest, Excel export) |
| `.github/workflows/daily.yml` | The daily schedule |
| `.github/ISSUE_TEMPLATE/log-post.yml` | The "Log a post" form |

Only free, public sources are used. Instagram is not scraped.
