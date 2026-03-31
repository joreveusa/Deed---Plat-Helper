import time, re
ts = int(time.time())
content = open('index.html', 'r', encoding='utf-8').read()
# Replace any existing versioned or unversioned app.js script tag
new_content = re.sub(r'<script src="app\.js(\?v=\d+)?"></script>', f'<script src="app.js?v={ts}"></script>', content)
if new_content != content:
    open('index.html', 'w', encoding='utf-8').write(new_content)
    print(f'Cache bust updated: ?v={ts}')
else:
    print('No change made')
    idx = content.find('app.js')
    print(repr(content[max(0,idx-20):idx+60]))
