# Enquiry hand-off

The enquiry tracker agent works from fintelius.net (an FP number exists only there). It cannot reach this database,
so for every FP number it finds, it writes `FP-<no>.json` here. After a `git pull`, run on the server:

    python run.py enquiry import

Safe to repeat: new FPs are added; known FPs only get their ack/docs flags raised and empty fields filled.
Fields: fp, company, region, country, contact, designation, email, phone, sr, ref, product, via, received (yyyy-mm-dd),
due, onedrive, outlook, notes, website, ack (0/1), docs (0/1). `fp` and `company` are required.
