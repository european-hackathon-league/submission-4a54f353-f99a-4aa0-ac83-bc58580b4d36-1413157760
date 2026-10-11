# Fetch plan (dry run)

Steps 1 to 9; 29 subjects (3 BMW lines, 26 competitors).

| Step | Source | Access | Adapter | Every (days) | Requests per run | Per day (average) | Backfill | Needs |
|---|---|---|---|---|---|---|---|---|
| 1 | `auto-data` | Scraping | _model_pages | 30 | 3 | 0 | 3 |  |
| 1 | `ev-database` | Scraping | _model_pages | 30 | 2 | 0 | 2 |  |
| 1 | `fueleconomy` | API | fueleconomy | 30 | 88 | 3 | 88 |  |
| 1 | `nhtsa-vpic` | API | vpic | 30 | 88 | 3 | 88 |  |
| 1 | `wikidata` | API | wikidata | 30 | 29 | 1 | 29 |  |
| 1 | `wikipedia` | API | wikipedia | 30 | 29 | 1 | 29 |  |
| 2 | `nhtsa-complaints` | API | nhtsa_complaints | 7 | 264 | 38 | 264 |  |
| 2 | `nhtsa-recalls` | API | nhtsa_recalls | 7 | 264 | 38 | 264 |  |
| 2 | `youtube-comments` | API | youtube_comments | 7 | 60 | 9 | 600 |  |
| 3 | `auto-china` | Scraping | fetch_newsroom | 30 | 11 | 0 | 201 |  |
| 3 | `autocar-new-cars` | Scraping | fetch_newsroom | 7 | 11 | 2 | 201 |  |
| 3 | `autoevolution-feed` | RSS | fetch_feed | 1 | 1 | 1 | 1 |  |
| 3 | `autoexpress-news` | Scraping | fetch_newsroom | 7 | 11 | 2 | 201 |  |
| 3 | `autohome-new` | Scraping, Chinese | fetch_newsroom | 7 | 11 | 2 | 201 |  |
| 3 | `bimmertoday` | RSS | fetch_feed | 1 | 1 | 1 | 1 |  |
| 3 | `bmwblog` | RSS | fetch_feed | 1 | 1 | 1 | 1 |  |
| 3 | `byd-site` | Scraping | fetch_newsroom | 7 | 11 | 2 | 201 |  |
| 3 | `caranddriver-future` | Scraping | fetch_newsroom | 7 | 11 | 2 | 201 |  |
| 3 | `carnewschina` | RSS, Scraping | fetch_feed | 1 | 1 | 1 | 1 |  |
| 3 | `carscoops` | RSS | fetch_feed | 1 | 1 | 1 | 1 |  |
| 3 | `ces` | Scraping | fetch_newsroom | 30 | 11 | 0 | 201 |  |
| 3 | `cnevpost` | RSS, Scraping | fetch_feed | 1 | 1 | 1 | 1 |  |
| 3 | `dongchedi-new` | Scraping, Chinese | fetch_newsroom | 7 | 11 | 2 | 201 |  |
| 3 | `electrek` | RSS | fetch_feed | 1 | 1 | 1 | 1 |  |
| 3 | `iaa` | Scraping | fetch_newsroom | 30 | 11 | 0 | 201 |  |
| 3 | `insideevs` | RSS | fetch_feed | 1 | 1 | 1 | 1 |  |
| 3 | `motor1` | RSS | fetch_feed | 1 | 1 | 1 | 1 |  |
| 3 | `nio-site` | Scraping | fetch_newsroom | 7 | 11 | 2 | 201 |  |
| 3 | `press-audi` | Scraping | fetch_newsroom | 7 | 11 | 2 | 201 |  |
| 3 | `press-bmw` | RSS, Scraping | fetch_feed | 1 | 1 | 1 | 1 |  |
| 3 | `press-genesis` | Scraping | fetch_newsroom | 7 | 11 | 2 | 201 |  |
| 3 | `press-lexus` | Scraping | fetch_newsroom | 7 | 11 | 2 | 201 |  |
| 3 | `press-mercedes` | Scraping | fetch_newsroom | 7 | 11 | 2 | 201 |  |
| 3 | `press-porsche` | Scraping | fetch_newsroom | 7 | 11 | 2 | 201 |  |
| 3 | `thedrive` | RSS | fetch_feed | 1 | 1 | 1 | 1 |  |
| 3 | `xiaomi-ev-site` | Scraping | fetch_newsroom | 7 | 11 | 2 | 201 |  |
| 3 | `zeekr-site` | Scraping | fetch_newsroom | 7 | 11 | 2 | 201 |  |
| 4 | `app-store` | RSS | fetch_app_store | 1 | 17 | 17 | 170 |  |
| 4 | `google-play` | Library | fetch_google_play | 1 | 10 | 10 | 100 |  |
| 5 | `genesis-owners` | Scraping | _xenforo | 1 | 20 | 20 | 4000 |  |
| 5 | `lucid-owners` | Scraping, RSS | fetch_feed | 1 | 1 | 1 | 1 |  |
| 5 | `motor-talk` | Scraping, German | motor_talk | 1 | 63 | 63 | 12000 |  |
| 5 | `pistonheads` | Scraping | pistonheads | 1 | 15 | 15 | 3000 |  |
| 5 | `tmc` | Scraping | _xenforo | 1 | 0 | 0 | 0 |  |
| 6 | `autoscout24` | Scraping | _model_pages | 7 | 44 | 6 | 804 |  |
| 6 | `carcomplaints` | Scraping | carcomplaints | 7 | 130 | 19 | 780 |  |
| 6 | `carwow` | Scraping | _magazine | 7 | 20 | 3 | 20 |  |
| 6 | `kbb` | Scraping | kbb | 7 | 24 | 3 | 24 |  |
| 6 | `parkers` | Scraping | _magazine | 7 | 20 | 3 | 20 |  |
| 6 | `whatcar` | Scraping | _magazine | 7 | 16 | 2 | 16 |  |
| 7 | `adac-autotest` | Scraping, German | _model_pages | 7 | 44 | 6 | 804 |  |
| 7 | `ams` | Scraping, German, partly paywalled | _model_pages | 7 | 44 | 6 | 804 |  |
| 7 | `autobild` | Scraping, German | _model_pages | 7 | 44 | 6 | 804 |  |
| 7 | `autocar` | Scraping | _magazine | 7 | 22 | 3 | 22 |  |
| 7 | `autoexpress` | Scraping | _magazine | 7 | 23 | 3 | 23 |  |
| 7 | `automobile-magazine` | Scraping, French | fetch_newsroom | 7 | 11 | 2 | 201 |  |
| 7 | `caranddriver` | Scraping | _magazine | 7 | 15 | 2 | 15 |  |
| 7 | `drive-uk` | Scraping | _magazine | 7 | 19 | 3 | 19 |  |
| 7 | `motortrend` | Scraping | _magazine | 7 | 15 | 2 | 15 |  |
| 7 | `teknikens-varld` | Scraping, Swedish | fetch_newsroom | 7 | 11 | 2 | 201 |  |
| 7 | `topgear` | Scraping | _magazine | 7 | 21 | 3 | 21 |  |
| 7 | `youtube-transcripts` | API, library | youtube_transcripts | 7 | 60 | 9 | 60 |  |
| 8 | `price-lists` | Download (PDF) | download folder | 30 | 0 | 0 | 0 |  |
| 9 | `adac-breakdown` | Scraping | fetch_market_page | 30 | 1 | 0 | 1 |  |
| 9 | `anfac` | Scraping | fetch_market_page | 30 | 1 | 0 | 1 |  |
| 9 | `capgemini` | Download (PDF) | download folder | 30 | 0 | 0 | 0 |  |
| 9 | `carfigures` | Scraping | fetch_market_page | 30 | 1 | 0 | 1 |  |
| 9 | `cox-automotive` | Scraping | fetch_market_page | 30 | 1 | 0 | 1 |  |
| 9 | `deloitte` | Download (PDF) | download folder | 30 | 0 | 0 | 0 |  |
| 9 | `ey` | Scraping | fetch_market_page | 30 | 1 | 0 | 1 |  |
| 9 | `goodcarbadcar` | Scraping | _model_pages | 30 | 3 | 0 | 3 |  |
| 9 | `google-trends` | Library | fetch_google_trends | 30 | 1 | 0 | 1 |  |
| 9 | `iea-ev-outlook` | Download (PDF, data) | download folder | 30 | 0 | 0 | 0 |  |
| 9 | `kba` | Download (Excel, PDF) | download folder | 30 | 0 | 0 | 0 |  |
| 9 | `mobility-sweden` | Scraping | fetch_market_page | 30 | 1 | 0 | 1 |  |
| 9 | `ofv` | Scraping | fetch_market_page | 30 | 1 | 0 | 1 |  |
| 9 | `pfa` | Scraping | fetch_market_page | 30 | 1 | 0 | 1 |  |
| 9 | `rai-bovag` | Scraping | fetch_market_page | 30 | 1 | 0 | 1 |  |
| 9 | `smmt` | Scraping | fetch_market_page | 30 | 1 | 0 | 1 |  |
| 9 | `tuv-report` | Scraping | fetch_market_page | 30 | 1 | 0 | 1 |  |
| 9 | `unrae` | Scraping | fetch_market_page | 30 | 1 | 0 | 1 |  |

Average: about 336 requests per day (each source only when due, see 'Every'); a day when every source is due would be about 1750; the daily budget FETCH_DAILY_REQUEST_BUDGET caps it either way, open cases first. One-time backfill: about 28735 requests, spread over days by the same budget (capped at 200 list pages per forum and subject). Sources that need a missing key are counted as 0.

Politeness: robots.txt respected, at least 2 s between requests per host, retries with backoff, raw responses stored before parsing.
