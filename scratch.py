import pathlib
path = pathlib.Path('j:/AI DATA CENTER/AI Agents/Deed & Plat Helper/app.js')
content = path.read_text(encoding='utf-8')

# Fix chunk 0
old_chunk_0 = "  const plssQuarters = _createPlssDynamicLayer('3', 0.45, 14);"
new_chunk_0 = old_chunk_0 + "\n  _propPicker.plssLayers = [plssTownships, plssSections, plssQuarters];"
content = content.replace(old_chunk_0, new_chunk_0)

# Fix chunk 1 which was malformed
# Let's remove the malformed togglePlssLayer and add it to the very end of the file.
malformed = '''
function togglePlssLayer() {
  const btn = document.getElementById("btnTogglePlss");
  if (!btn || !_propPicker.map || !_propPicker.plssLayers) return;

  const isActive = _propPicker.map.hasLayer(_propPicker.plssLayers[0]);
  
  if (isActive) {
    _propPicker.plssLayers.forEach(l => _propPicker.map.removeLayer(l));
    btn.style.background = "rgba(26,26,46,0.9)";
    btn.style.color = "#56d3a0";
  } else {
    _propPicker.plssLayers.forEach(l => _propPicker.map.addLayer(l));
    btn.style.background = "rgba(86,211,160,0.2)";
    btn.style.color = "#ffffff";
  }
}'''
if malformed in content:
    content = content.replace(malformed, "")

content += "\n" + malformed + "\n"

path.write_text(content, encoding='utf-8')
print('Patched app.js successfully')