# Poshmark Closet Scraper

Python scripts to scrape listing information from Poshmark closet pages.

## Features

- **Filters for available items only** (uses `?availability=available` parameter)
- **Handles lazy loading/pagination** - automatically fetches multiple pages of listings
- Scrapes listing titles, prices, sizes, brands, conditions, and engagement metrics
- **Visits each individual listing page** to extract detailed information
- Extracts comprehensive details: descriptions, images, tags, seller info, and more
- Uses only `requests` and `BeautifulSoup` - no browser automation needed
- Exports data to JSON or CSV format
- **AWS S3 integration** - store listings in S3 with slug-based organization
- **Incremental updates** - only process new listings by comparing with existing S3 slugs
- **AWS Lambda ready** - includes Lambda handler function
- Command-line interface with customizable options
- Configurable delays between requests to be respectful to the server

## Installation

1. Install the required dependencies:

```bash
pip install -r requirements.txt
```

## Usage

### Basic Usage

```bash
python poshmark_scraper.py emily2636
```

### Command-Line Options

Both scripts support the following options:

- `username`: Poshmark closet username (default: emily2636)
- `--format`: Output format - `json`, `csv`, or `both` (default: json)
- `--output` or `-o`: Custom output filename (without extension)
- `--no-details`: Skip visiting individual listing pages (faster, but less detailed)
- `--delay`: Delay between requests in seconds (default: 2.0)
- `--max-pages`: Maximum number of pages to fetch for lazy loading (default: 200)
- `--s3-bucket`: S3 bucket name for storing listings
- `--s3-prefix`: S3 prefix/path for storing listings (default: username)
- `--s3-only`: Only save to S3, skip local file output
- `--incremental`: Only process new listings (compare with existing S3 slugs) (default: True)
- `--no-incremental`: Process all listings, even if they exist in S3

### Examples

```bash
# Scrape emily2636 closet and save as JSON
python poshmark_scraper.py emily2636

# Scrape a different closet and save as CSV
python poshmark_scraper.py another_username --format csv

# Save with custom filename
python poshmark_scraper.py emily2636 --output my_listings --format both

# Skip visiting individual listing pages (faster, less detailed)
python poshmark_scraper.py emily2636 --no-details

# Adjust delay between requests (be respectful!)
python poshmark_scraper.py emily2636 --delay 3.0

# Fetch more pages (for closets with many listings)
python poshmark_scraper.py emily2636 --max-pages 20

# Save to S3 with incremental updates
python poshmark_scraper.py emily2636 --s3-bucket my-poshmark-bucket --s3-prefix listings/emily2636

# Save to S3 only (no local files)
python poshmark_scraper.py emily2636 --s3-bucket my-poshmark-bucket --s3-only

# Process all listings (ignore existing in S3)
python poshmark_scraper.py emily2636 --s3-bucket my-poshmark-bucket --no-incremental
```

## Output Format


The scraper extracts the following information for each listing:

**Basic information (from closet page):**
- `title`: Listing title
- `price`: Current price
- `original_price`: Original/retail price (if available)
- `size`: Item size
- `brand`: Brand name
- `condition`: Condition (NWT, Flawed, etc.)
- `likes`: Number of likes
- `comments`: Number of comments
- `link`: Direct link to the listing

**Detailed information (when visiting individual listing pages):**
- `url`: Full listing URL
- `description`: Full item description
- `images`: List of all image URLs
- `category`: Item category
- `seller`: Seller username
- `shares`: Number of shares
- `tags`: List of tags associated with the listing
- `availability`: Availability status
- `shipping`: Shipping information

## S3 Storage

The scraper can store listings in AWS S3 with the following features:

- **Slug-based storage**: Each listing is stored as a separate JSON file using its unique ID as the filename
- **Incremental updates**: Automatically compares with existing listings in S3 and only processes new ones
- **Organized structure**: Listings are stored under `{s3_prefix}/{listing_id}.json`

### S3 Storage Structure

```
s3://my-bucket/
  emily2636/
    68f06282f59cc4ded765a577.json
    68f40bd0a56649ae6101c747.json
    ...
```

### Environment Variables

You can also configure S3 using environment variables:
- `S3_BUCKET`: S3 bucket name
- `S3_PREFIX`: S3 prefix/path (defaults to username if not set)

## AWS Lambda

The scraper includes a Lambda handler function. To use it:

1. Package the script with dependencies
2. Set up Lambda function with appropriate IAM permissions (S3 read/write)
3. Configure environment variables or pass in event

### Lambda Event Structure

```json
{
  "username": "emily2636",
  "s3_bucket": "my-poshmark-bucket",
  "s3_prefix": "listings/emily2636",
  "incremental": true,
  "delay": 1.0,
  "max_pages": 200
}
```

### Lambda IAM Permissions

Your Lambda execution role needs:
- `s3:PutObject` on your bucket
- `s3:GetObject` on your bucket
- `s3:ListBucket` on your bucket

## Notes

- Poshmark may have rate limiting or anti-scraping measures. Use responsibly.
- The scraper automatically handles lazy loading using Poshmark's API.
- If you find that not all listings are being scraped, try increasing `--max-pages`.
- The scraper extracts high-resolution images when available.
- For S3 usage, ensure AWS credentials are configured (via `~/.aws/credentials`, environment variables, or IAM role for Lambda).

