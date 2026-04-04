"""One-time script to remove Stripe + Admin routes from app.py.
They've been moved to routes/stripe.py and routes/admin.py Blueprints."""


with open("app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

# Find the exact line indices
stripe_start = None
team_start = None

for i, line in enumerate(lines):
    # "Stripe billing" section header (not the import at top)
    if "Stripe billing" in line and i > 500 and stripe_start is None:
        stripe_start = i
    # "Team management" section header
    if "Team management" in line and i > 600:
        team_start = i
        break

print(f"Stripe section: line {stripe_start+1}")
print(f"Team section:   line {team_start+1}")
print(f"Removing lines {stripe_start+1} to {team_start}")

# Replace the Stripe+Admin section with comments
replacement = [
    "\n",
    "# \u2500\u2500 Stripe billing \u2192 moved to routes/stripe.py Blueprint \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n",
    "# \u2500\u2500 Admin panel   \u2192 moved to routes/admin.py Blueprint \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n",
    "\n",
]

new_lines = lines[:stripe_start] + replacement + lines[team_start:]

with open("app.py", "w", encoding="utf-8") as f:
    f.writelines(new_lines)

print(f"Done. Old: {len(lines)} lines -> New: {len(new_lines)} lines")
print(f"Removed {len(lines) - len(new_lines)} lines")
