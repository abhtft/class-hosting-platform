p = r"D:\Github_Codes\class-hosting-platform-github\lambda\trace_core.py"
lines = open(p, encoding="utf8").read().split("\n")
new = {
    "IFSC": r'''    (re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"), "[bank]"),                  # IFSC code''',
    "gstin": r'''    (re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b"), "[gstin]"),''',
    "passport": r'''    (re.compile(r"\b[A-Z][0-9]{7}\b"), "[passport]"),''',
    "upi": r'''    (re.compile(r"\b[\w.-]{2,}@[a-z]{2,}\b"), "[upi]"),                  # UPI id such as name@bank (after the email rule)''',
}
out = []
for line in lines:
    for key, text in new.items():
        if f'"[{key}]"' in line or (key == "IFSC" and '"[bank]"' in line) or (key == "upi" and '"[upi]"' in line):
            line = text
            break
    out.append(line)
open(p, "w", encoding="utf8").write("\n".join(out))
