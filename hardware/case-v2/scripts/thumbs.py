import glob, os, sys
from PIL import Image
d = sys.argv[1]
for p in sorted(glob.glob(os.path.join(d, 'IMG*.jpg'))):
    if p.endswith('_s.jpg'):
        continue
    im = Image.open(p); im.thumbnail((1400, 1400)); im.save(p[:-4] + '_s.jpg', quality=85); print(os.path.basename(p), im.size)
