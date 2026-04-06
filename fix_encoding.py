"""Fix corrupted UTF-8 separator comment in app.py."""
with open('app.py', 'rb') as f:
    data = f.read()

lines = data.split(b'\n')
fixed = 0
for i, line in enumerate(lines):
    if b'\x80' in line and line.strip().startswith(b'#'):
        # Replace the entire corrupted comment line with a clean separator
        indent = b'\r' if line.endswith(b'\r') else b''
        lines[i] = b'# ' + (b'\xe2\x94\x80' * 50) + indent
        print(f'Fixed line {i+1}')
        fixed += 1

print(f'Total fixed: {fixed}')
with open('app.py', 'wb') as f:
    f.write(b'\n'.join(lines))
print('Saved app.py')
