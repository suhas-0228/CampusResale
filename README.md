# CampusResale — Flask project

This is a working Flask + SQLite implementation based on the project specification supplied.

## Run on macOS

```bash
cd CampusResale_Full_Project
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000

The database (`campusresale.db`) and uploaded images (`uploads/`) are created automatically. On first startup, six clearly described sample products are added under a non-login demo seller so the marketplace is not empty. After a user logs in, the navigation bar displays Selling, Sold items, Bought items, My profile and Customer support; before login, only Marketplace is shown.

## Optional configuration

Set a strong session secret before deployment:

```bash
export SECRET_KEY="replace-with-a-long-random-secret"
```

To restrict registration to a college email domain (optional):

```bash
export COLLEGE_EMAIL_DOMAIN="yourcollege.edu"
```

To enable customer-support email, use a Gmail App Password (not your normal Gmail password):

```bash
export GMAIL_ADDRESS="your-sender@gmail.com"
export GMAIL_APP_PASSWORD="your-16-character-app-password"
```

Support messages are sent to `suhasraja10@gmail.com`. The sender's email is used as Reply-To.

## Implemented

- College-email registration (optional domain restriction), login/logout, Werkzeug password hashing
- SQLite persistence and REST-style JSON endpoints
- Listing creation with category, condition, age, original price, image, description, semester-end flag and required Indian mobile number
- Search, filters and sorting; seller contact details only in authenticated responses
- Rule-based price estimate and 60/40 blending with median after five comparable active listings
- Seller ratings (one per rater/seller, editable), reports and automatic hiding at three reports
- Seller-only sold/delete controls; optional buyer email links purchase history; buyer-linked sold listings cannot be deleted
- Account counters, selling/sold/bought/profile/support navigation
- Customer-support email with one-message-per-minute session limit
- Responsive warm-white, black and gold interface; light/dark theme

## Honest limitations / deployment notes

- Price recommendation is rule-based, not a trained ML model. Depreciation/default prices are configurable estimates.
- Registration does not verify email addresses or send a verification code.
- Purchase records are created when the seller enters a buyer's registered email. No payment or in-app chat exists.
- Suspicious listings are handled through the specified price flag and user reports; no image/text AI moderation is included.
- SQLite and this single-file Flask application are intended as a one-campus prototype. Production deployment should use a production WSGI server, secure environment secrets, HTTPS, backups and a production database.
- This starter has no payment gateway. Do not use demo deployment settings or the default secret key for real users.
