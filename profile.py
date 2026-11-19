"""Facts about Fintelius used in emails. Edit here, not in the prompts."""

COMPANY = "Fintelius Engineering Private Limited"
SENDER_NAME = "Swapnil Patel"
SENDER_TITLE = "Managing Director"
WEBSITE = "www.fintelius.com"
REPLY_TO = "swapnil@fintelius.com"
PHONES = "+91 9227994511"
ADDRESS = ("Plot no. 4, 5 & 46 C, Akshar Industrial Park, opp. Zydus Cadila Healthcare, "
           "Chachrawadi Vasna, Sarkhej Bavla Highway, Ahmedabad 382213, Gujarat, India")
ADNOC_AVL = "0020004813"
SIGN_ADDRESS = "Ahmedabad, Gujarat, India"

PRODUCTS = [
    "Air Cooled Heat Exchangers",
    "Shell and Tube Heat Exchangers",
    "Shell and Fin Exchangers",
    "Pressure Vessels",
    "Process Skids and Piping Spools",
    "Finned Tubes",
]

FACTS = (
    "Fintelius Engineering Private Limited, Ahmedabad, India, established 2012. "
    "Manufacturer of air cooled heat exchangers, shell and tube heat exchangers, shell and fin exchangers, "
    "pressure vessels, process skids and piping spools, and finned tubes. "
    "Serves oil and gas, refineries and petrochemicals, fertilisers and power. "
    "Approx. 40,000 sq ft covered manufacturing area. ADNOC AVL no. " + ADNOC_AVL + "."
)


def signature_text():
    return (
        f"Best regards,\n{SENDER_NAME}\n{SENDER_TITLE}\n{COMPANY}\n"
        f"{WEBSITE} | {REPLY_TO} | {PHONES}\n{SIGN_ADDRESS}"
    )


def signature_html():
    return (
        f"<p>Best regards,<br><b>{SENDER_NAME}</b><br>{SENDER_TITLE}<br>{COMPANY}<br>"
        f'<a href="https://{WEBSITE}">{WEBSITE}</a> | {REPLY_TO} | {PHONES}<br>{SIGN_ADDRESS}</p>'
    )


UNSUB_LINE = "If this is not relevant, please let us know and we will not write again."

APPROVALS = "ASME U, U2 and R stamps, NB, IBR and ISO 9001:2015"
ADNOC_CATEGORIES = (
    "141044: Heat Exchangers - Shell & Tube\n"
    "141064: Air Coolers - Finned Tubed\n"
    "141046: Tube Bundles - For Heat Exchangers and Coolers\n"
    "161425: Tubes - Finned (All Metals)")
