p = r"D:\Github_Codes\class-hosting-platform-github\tests\test_spec.py"
s = open(p, encoding="utf8").read()
a = '''for name, expected in facts["apiThrottles"].items():
    text = block_of(name)
    check(f"apiThrottles.{name}.rate", expected["rate"], int(re.search(r"ThrottlingRateLimit: (\\d+)", text).group(1)))
    check(f"apiThrottles.{name}.burst", expected["burst"], int(re.search(r"ThrottlingBurstLimit: (\\d+)", text).group(1)))
'''
assert a in s
b = '''def throttle(text, key):
    """The number written in the template, or (for the backend API, whose caps are parameters the installer sets) the parameter's default."""
    literal = re.search(rf"{key}: (\\d+)", text)
    if literal:
        return int(literal.group(1))
    ref = re.search(rf"{key}: !Ref (\\w+)", text).group(1)
    return int(re.search(rf"\\n  {ref}:\\n    Type: Number\\n    Default: (\\d+)", template).group(1))


for name, expected in facts["apiThrottles"].items():
    text = block_of(name)
    check(f"apiThrottles.{name}.rate", expected["rate"], throttle(text, "ThrottlingRateLimit"))
    check(f"apiThrottles.{name}.burst", expected["burst"], throttle(text, "ThrottlingBurstLimit"))
'''
s = s.replace(a, b, 1)
open(p, "w", encoding="utf8").write(s)
print("ok")
