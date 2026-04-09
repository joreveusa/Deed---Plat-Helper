import sys, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
import httpx

# Test arXiv directly (the same logic the Blueprint uses)
resp = httpx.get("https://export.arxiv.org/api/query",
                 params={"search_query": "all:GPS coordinate accuracy surveying",
                         "max_results": 3},
                 timeout=10)
print(f"arXiv status: {resp.status_code}")

from bs4 import BeautifulSoup
soup = BeautifulSoup(resp.text, "xml")
papers = [(e.find("title") or {}).get_text(strip=True) for e in soup.find_all("entry")]
for t in papers:
    print(f"  - {t[:75]}")

print(f"\nBlueprint importable: ", end="")
try:
    from routes.research import research_bp
    print("YES - OK")
except Exception as e:
    print(f"NO -- {e}")


