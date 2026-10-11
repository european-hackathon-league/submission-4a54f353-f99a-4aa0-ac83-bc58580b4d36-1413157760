# Fetch plan (dry run)

Steps 1 to 9; 29 subjects (3 BMW lines, 26 competitors).

| Step | Source | Access | Adapter | Requests incremental | Requests backfill | Needs |
|---|---|---|---|---|---|---|
| 1 | `auto-data` | Scraping | _model_pages | 3 | 3 |  |
| 1 | `ev-database` | Scraping | _model_pages | 2 | 2 |  |
| 1 | `fueleconomy` | API | fueleconomy | 116 | 116 |  |
| 1 | `nhtsa-vpic` | API | vpic | 116 | 116 |  |
| 1 | `wikidata` | API | wikidata | 29 | 29 |  |
| 1 | `wikipedia` | API | wikipedia | 29 | 29 |  |
| 2 | `nhtsa-complaints` | API | nhtsa_complaints | 348 | 348 |  |
| 2 | `nhtsa-recalls` | API | nhtsa_recalls | 348 | 348 |  |
| 2 | `reddit` | API | reddit | 464 | 4640 | Reddit OAuth app |
| 2 | `youtube-comments` | API | youtube_comments | 580 | 5800 | YouTube Data API key |
| 3 | `auto-china` | Scraping | fetch_newsroom | 16 | 201 |  |
| 3 | `autocar-new-cars` | Scraping | fetch_newsroom | 16 | 201 |  |
| 3 | `autoevolution-feed` | RSS | fetch_feed | 1 | 1 |  |
| 3 | `autoexpress-news` | Scraping | fetch_newsroom | 16 | 201 |  |
| 3 | `autohome-new` | Scraping, Chinese | fetch_newsroom | 16 | 201 |  |
| 3 | `bimmertoday` | RSS | fetch_feed | 1 | 1 |  |
| 3 | `bmwblog` | RSS | fetch_feed | 1 | 1 |  |
| 3 | `byd-site` | Scraping | fetch_newsroom | 16 | 201 |  |
| 3 | `caranddriver-future` | Scraping | fetch_newsroom | 16 | 201 |  |
| 3 | `carnewschina` | RSS, Scraping | fetch_feed | 1 | 1 |  |
| 3 | `carscoops` | RSS | fetch_feed | 1 | 1 |  |
| 3 | `ces` | Scraping | fetch_newsroom | 16 | 201 |  |
| 3 | `cnevpost` | RSS, Scraping | fetch_feed | 1 | 1 |  |
| 3 | `dongchedi-new` | Scraping, Chinese | fetch_newsroom | 16 | 201 |  |
| 3 | `electrek` | RSS | fetch_feed | 1 | 1 |  |
| 3 | `iaa` | Scraping | fetch_newsroom | 16 | 201 |  |
| 3 | `insideevs` | RSS | fetch_feed | 1 | 1 |  |
| 3 | `motor1` | RSS | fetch_feed | 1 | 1 |  |
| 3 | `nio-site` | Scraping | fetch_newsroom | 16 | 201 |  |
| 3 | `press-audi` | Scraping | fetch_newsroom | 16 | 201 |  |
| 3 | `press-bmw` | RSS, Scraping | fetch_feed | 1 | 1 |  |
| 3 | `press-genesis` | Scraping | fetch_newsroom | 16 | 201 |  |
| 3 | `press-lexus` | Scraping | fetch_newsroom | 16 | 201 |  |
| 3 | `press-mercedes` | Scraping | fetch_newsroom | 16 | 201 |  |
| 3 | `press-porsche` | Scraping | fetch_newsroom | 16 | 201 |  |
| 3 | `thedrive` | RSS | fetch_feed | 1 | 1 |  |
| 3 | `xiaomi-ev-site` | Scraping | fetch_newsroom | 16 | 201 |  |
| 3 | `zeekr-site` | Scraping | fetch_newsroom | 16 | 201 |  |
| 4 | `app-store` | RSS | fetch_app_store | 17 | 170 |  |
| 4 | `google-play` | Library | fetch_google_play | 10 | 100 |  |
| 5 | `genesis-owners` | Scraping | _xenforo | 290 | 58000 |  |
| 5 | `lucid-owners` | Scraping, RSS | fetch_feed | 1 | 1 |  |
| 5 | `motor-talk` | Scraping, German | motor_talk | 300 | 12000 |  |
| 5 | `pistonheads` | Scraping | pistonheads | 30 | 3000 |  |
| 5 | `tmc` | Scraping | _xenforo | 290 | 58000 |  |
| 6 | `autoscout24` | Scraping | _model_pages | 64 | 804 |  |
| 6 | `carcomplaints` | Scraping | carcomplaints | 160 | 960 |  |
| 6 | `carwow` | Scraping | _magazine | 20 | 20 |  |
| 6 | `kbb` | Scraping | kbb | 28 | 28 |  |
| 6 | `parkers` | Scraping | _magazine | 20 | 20 |  |
| 6 | `whatcar` | Scraping | _magazine | 16 | 16 |  |
| 7 | `adac-autotest` | Scraping, German | _model_pages | 64 | 804 |  |
| 7 | `ams` | Scraping, German, partly paywalled | _model_pages | 64 | 804 |  |
| 7 | `autobild` | Scraping, German | _model_pages | 64 | 804 |  |
| 7 | `autocar` | Scraping | _magazine | 22 | 22 |  |
| 7 | `autoexpress` | Scraping | _magazine | 23 | 23 |  |
| 7 | `automobile-magazine` | Scraping, French | fetch_newsroom | 16 | 201 |  |
| 7 | `caranddriver` | Scraping | _magazine | 19 | 19 |  |
| 7 | `drive-uk` | Scraping | _magazine | 19 | 19 |  |
| 7 | `motortrend` | Scraping | _magazine | 19 | 19 |  |
| 7 | `teknikens-varld` | Scraping, Swedish | fetch_newsroom | 16 | 201 |  |
| 7 | `topgear` | Scraping | _magazine | 21 | 21 |  |
| 7 | `youtube-transcripts` | API, library | youtube_transcripts | 580 | 580 |  |
| 8 | `price-lists` | Download (PDF) | download folder | 0 | 0 |  |
| 9 | `adac-breakdown` | Scraping | fetch_market_page | 1 | 1 |  |
| 9 | `anfac` | Scraping | fetch_market_page | 1 | 1 |  |
| 9 | `capgemini` | Download (PDF) | download folder | 0 | 0 |  |
| 9 | `carfigures` | Scraping | fetch_market_page | 1 | 1 |  |
| 9 | `cox-automotive` | Scraping | fetch_market_page | 1 | 1 |  |
| 9 | `deloitte` | Download (PDF) | download folder | 0 | 0 |  |
| 9 | `ey` | Scraping | fetch_market_page | 1 | 1 |  |
| 9 | `goodcarbadcar` | Scraping | _model_pages | 3 | 3 |  |
| 9 | `google-trends` | Library | fetch_google_trends | 1 | 1 |  |
| 9 | `iea-ev-outlook` | Download (PDF, data) | download folder | 0 | 0 |  |
| 9 | `kba` | Download (Excel, PDF) | download folder | 0 | 0 |  |
| 9 | `mobility-sweden` | Scraping | fetch_market_page | 1 | 1 |  |
| 9 | `ofv` | Scraping | fetch_market_page | 1 | 1 |  |
| 9 | `pfa` | Scraping | fetch_market_page | 1 | 1 |  |
| 9 | `rai-bovag` | Scraping | fetch_market_page | 1 | 1 |  |
| 9 | `smmt` | Scraping | fetch_market_page | 1 | 1 |  |
| 9 | `tuv-report` | Scraping | fetch_market_page | 1 | 1 |  |
| 9 | `unrae` | Scraping | fetch_market_page | 1 | 1 |  |

Total: about 4507 requests per incremental run, about 151511 for the one-time backfill (capped at 200 list pages per forum and subject; daily budget FETCH_DAILY_REQUEST_BUDGET).

Politeness: robots.txt respected, at least 2 s between requests per host, retries with backoff, raw responses stored before parsing.
