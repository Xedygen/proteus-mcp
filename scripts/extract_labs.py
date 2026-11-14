import zipfile, sys, os, glob
root, out = sys.argv[1], sys.argv[2]
for p in sorted(glob.glob(os.path.join(root, "**", "*.pdsprj"), recursive=True)):
    rel = os.path.relpath(p, root).replace("\\", "/")
    tag = rel.replace("/", "__").replace(" ", "").replace(".pdsprj", "")
    tag = tag.encode("ascii", "ignore").decode()
    z = zipfile.ZipFile(p)
    d = os.path.join(out, tag); os.makedirs(d, exist_ok=True)
    z.extractall(d)
    names = [f"{i.filename}({i.file_size})" for i in z.infolist()]
    print(tag, "|", " ".join(names))
