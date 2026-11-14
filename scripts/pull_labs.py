import json, os, sys, urllib.request, urllib.parse
REPO = "baselkelziye/YTU_Bilgisayar_Muhendisligi_Arsiv"
PARENT = "3-1/Mikroişlemci Sistemleri ve Assembly Dili"
NAME = "lablar"
dest, mode = sys.argv[1], sys.argv[2]
def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "lab-fetch"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()
q = urllib.parse.quote
parent = json.loads(get(f"https://api.github.com/repos/{REPO}/contents/{q(PARENT)}?ref=main"))
print("siblings:", [(e["name"], e["type"]) for e in parent])
sha = next(e["sha"] for e in parent if e["name"] == NAME)
tree = json.loads(get(f"https://api.github.com/repos/{REPO}/git/trees/{sha}?recursive=1"))
print("truncated:", tree.get("truncated"))
blobs = [e for e in tree["tree"] if e["type"] == "blob"]
total = sum(e.get("size", 0) for e in blobs)
print(f"{len(blobs)} files, {total/1e6:.1f} MB")
if mode == "list":
    years = {}
    for e in blobs:
        y = e["path"].split("/")[0]
        years.setdefault(y, [0, 0]); years[y][0] += 1; years[y][1] += e.get("size", 0)
    for y, (n, s) in sorted(years.items()): print(f"  {y}: {n} files, {s/1e6:.1f} MB")
    big = sorted(blobs, key=lambda e: -e.get("size", 0))[:10]
    for e in big: print(f"  big {e['size']/1e6:.1f} MB {e['path']}")
    sys.exit()
bad = 0
for e in blobs:
    out = os.path.join(dest, *e["path"].split("/"))
    if os.path.exists(out) and os.path.getsize(out) == e["size"]:
        continue
    os.makedirs(os.path.dirname(out), exist_ok=True)
    url = f"https://raw.githubusercontent.com/{REPO}/main/{q(PARENT + '/' + NAME + '/' + e['path'])}"
    try:
        data = get(url)
    except Exception as ex:
        bad += 1; print("FAIL", e["path"], ex); continue
    with open(out, "wb") as f: f.write(data)
    if len(data) != e["size"]:
        bad += 1; print("SIZE MISMATCH", e["path"], len(data), e["size"])
print("done, problems:", bad)
