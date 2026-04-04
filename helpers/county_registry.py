"""
helpers/county_registry.py — Curated county ArcGIS & records portal registry.

Each entry contains:
  - fips:          5-digit FIPS code (state+county)
  - name:          "County Name, ST"
  - state:         2-letter state code
  - arcgis_url:    Full ArcGIS REST query URL for the parcel layer
  - arcgis_fields: Concept → actual field name mapping (partial overrides of defaults)
  - portal_url:    County records portal base URL (e.halFILE or similar)
  - portal_type:   "ehalfile" | "tyler" | "laserfish" | "custom" | "unknown"
  - notes:         Human-readable notes for the user

Field concepts (keys in arcgis_fields):
  parcel_id, owner, address_all, address1, street_no, street_name,
  city, zipcode, legal, area, subdivision, zoning, land_use,
  township, twp_dir, range, rng_dir, section,
  struct_count, struct_type, owner_type, mail_addr

A missing field concept falls back to the Taos NM default in _get_arcgis_config().
"""

from __future__ import annotations

# ── Registry ──────────────────────────────────────────────────────────────────
# New Mexico counties using the NM OSE statewide parcel service.
# Layer numbers differ per county within the same MapServer.
# Source: https://gis.ose.nm.gov/server_s/rest/services/Parcels/County_Parcels_2025/MapServer
# Layer index reference (as of 2025):
#   0=Bernalillo  1=Catron  2=Chaves  3=Cibola  4=Colfax  5=Curry
#   6=De Baca  7=Dona Ana  8=Eddy  9=Grant  10=Guadalupe  11=Harding
#   12=Hidalgo  13=Lea  14=Lincoln  15=Los Alamos  16=Luna  17=McKinley
#   18=Mora  19=Otero  20=Quay  21=Rio Arriba  22=Roosevelt  23=Sandoval
#   24=San Juan  25=San Miguel  26=Santa Fe  27=Sierra  28=Socorro
#   29=Taos  30=Torrance  31=Union  32=Valencia

_NM_OSE_BASE = (
    "https://gis.ose.nm.gov/server_s/rest/services/"
    "Parcels/County_Parcels_2025/MapServer/{layer}/query"
)

# NM OSE fields are uniform across all layers (same schema)
_NM_OSE_FIELDS: dict[str, str] = {
    "parcel_id":   "UPC",
    "owner":       "OwnerAll",
    "address_all": "SitusAddressAll",
    "address1":    "SitusAddress1",
    "street_no":   "SitusStreetNumber",
    "street_name": "SitusStreetName",
    "city":        "SitusCity",
    "zipcode":     "SitusZipCode",
    "legal":       "LegalDescription",
    "area":        "LandArea",
    "subdivision": "Subdivision",
    "zoning":      "ZoningDescription",
    "land_use":    "LandUseDescription",
    "township":    "Township",
    "twp_dir":     "TownshipDirection",
    "range":       "Range",
    "rng_dir":     "RangeDirection",
    "section":     "Section",
    "struct_count": "StructureCount",
    "struct_type":  "StructureType",
    "owner_type":   "OwnerType",
    "mail_addr":    "MailAddressAll",
}

def _nm(county: str, layer: int, fips: str,
        portal_url: str = "", portal_type: str = "ehalfile",
        notes: str = "") -> dict:
    """Helper to build a NM OSE entry."""
    return {
        "fips":         fips,
        "name":         f"{county} County, NM",
        "state":        "NM",
        "arcgis_url":   _NM_OSE_BASE.format(layer=layer),
        "arcgis_fields": _NM_OSE_FIELDS,
        "portal_url":   portal_url,
        "portal_type":  portal_type,
        "notes":        notes or f"NM OSE statewide parcel service, layer {layer}.",
    }


COUNTY_REGISTRY: list[dict] = [

    # ── New Mexico ────────────────────────────────────────────────────────────
    _nm("Bernalillo", 0,  "35001",
        portal_url="http://records.bernalillo.nm.gov",
        notes="Albuquerque metro. Uses 1stNMTitle / e.halFILE portal."),

    _nm("Catron",     1,  "35003"),
    _nm("Chaves",     2,  "35005",
        notes="Roswell area."),
    _nm("Cibola",     3,  "35006"),
    _nm("Colfax",     4,  "35007"),
    _nm("Curry",      5,  "35009",
        notes="Clovis area."),
    _nm("De Baca",    6,  "35011"),
    _nm("Doña Ana",   7,  "35013",
        notes="Las Cruces area."),
    _nm("Eddy",       8,  "35015",
        notes="Carlsbad / Artesia area. Active oil & gas county."),
    _nm("Grant",      9,  "35017",
        notes="Silver City area."),
    _nm("Guadalupe", 10,  "35019"),
    _nm("Harding",   11,  "35021"),
    _nm("Hidalgo",   12,  "35023"),
    _nm("Lea",       13,  "35025",
        notes="Lovington / Hobbs area. Active oil & gas county."),
    _nm("Lincoln",   14,  "35027"),
    _nm("Los Alamos",15,  "35028"),
    _nm("Luna",      16,  "35029"),
    _nm("McKinley",  17,  "35031",
        notes="Gallup area."),
    _nm("Mora",      18,  "35033"),
    _nm("Otero",     19,  "35035",
        notes="Alamogordo area."),
    _nm("Quay",      20,  "35037"),
    _nm("Rio Arriba",21,  "35039",
        notes="Española / Chama area. Active surveying zone."),
    _nm("Roosevelt", 22,  "35041"),
    _nm("Sandoval",  23,  "35043",
        portal_url="http://records.1stnmtitle.com",
        notes="Rio Rancho area. Uses 1stNMTitle portal."),
    _nm("San Juan",  24,  "35045",
        notes="Farmington area. Active oil & gas county."),
    _nm("San Miguel",25,  "35047",
        notes="Las Vegas NM area."),
    _nm("Santa Fe",  26,  "35049",
        notes="State capital. Heavy surveying activity."),
    _nm("Sierra",    27,  "35051"),
    _nm("Socorro",   28,  "35053"),
    _nm("Taos",      29,  "35055",
        portal_url="http://records.1stnmtitle.com",
        notes="Primary development county. Uses 1stNMTitle / e.halFILE."),
    _nm("Torrance",  30,  "35057"),
    _nm("Union",     31,  "35059"),
    _nm("Valencia",  32,  "35061",
        notes="Belen / Los Lunas area."),

    # ── Colorado ──────────────────────────────────────────────────────────────
    # CO counties generally host their own ArcGIS servers.
    # Source: county GIS portals — verify URLs before use.
    {
        "fips":         "08041",
        "name":         "El Paso County, CO",
        "state":        "CO",
        "arcgis_url":   (
            "https://gis.elpasoco.com/arcgis/rest/services/"
            "PublicData/Parcels/MapServer/0/query"
        ),
        "arcgis_fields": {
            "parcel_id":   "SCHEDULE",
            "owner":       "OWNER1",
            "address_all": "SITUS_ADDR",
            "legal":       "LEGAL_DESC",
            "area":        "LAND_SQFT",
            "subdivision": "SUBDIV_NM",
            "city":        "SITUS_CITY",
            "zipcode":     "SITUS_ZIP",
        },
        "portal_url":  "https://recording.elpasoco.com",
        "portal_type": "tyler",
        "notes":       "Colorado Springs area. Tyler iQS records portal. APN field = SCHEDULE.",
    },
    {
        "fips":         "08059",
        "name":         "Jefferson County, CO",
        "state":        "CO",
        "arcgis_url":   (
            "https://services1.arcgis.com/RojbBXMv5RGBR44n/arcgis/rest/services/"
            "JeffCoParcelFeatureService/FeatureServer/0/query"
        ),
        "arcgis_fields": {
            "parcel_id":   "APN",
            "owner":       "OWNERNM",
            "address_all": "SITEADDRESS",
            "legal":       "LEGALDESC",
            "area":        "PARCEL_AREA",
            "city":        "SITECITY",
            "zipcode":     "SITEZIP",
        },
        "portal_url":  "https://recording.jeffco.us",
        "portal_type": "tyler",
        "notes":       "Golden / Lakewood area. APN field used as parcel ID.",
    },
    {
        "fips":         "08031",
        "name":         "Denver County, CO",
        "state":        "CO",
        "arcgis_url":   (
            "https://services1.arcgis.com/zdq4fDjsGk5P5QMZV/arcgis/rest/services/"
            "Parcels/FeatureServer/0/query"
        ),
        "arcgis_fields": {
            "parcel_id":   "PARCEL_NUM",
            "owner":       "OWN_NAME1",
            "address_all": "FULL_ADDRESS",
            "legal":       "LEGAL_DESC",
            "area":        "SHAPE_Area",
            "city":        "CITY",
            "zipcode":     "ZIPCODE",
        },
        "portal_url":  "https://www.denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Clerk-and-Recorder",
        "portal_type": "custom",
        "notes":       "Denver county. Custom portal. ArcGIS hosted on ArcGIS Online.",
    },
    {
        "fips":         "08001",
        "name":         "Adams County, CO",
        "state":        "CO",
        "arcgis_url":   (
            "https://gis.adcogov.org/arcgis/rest/services/"
            "Parcels/ParcelQuery/MapServer/0/query"
        ),
        "arcgis_fields": {
            "parcel_id":   "AccountNum",
            "owner":       "Owner",
            "address_all": "SitusAddress",
            "legal":       "LegalDescription",
            "subdivision": "Subdivision",
            "city":        "SitusCity",
            "zipcode":     "SitusZip",
        },
        "portal_url":  "https://recording.adcogov.org",
        "portal_type": "tyler",
        "notes":       "Commerce City / Westminster area.",
    },

    # ── Texas ─────────────────────────────────────────────────────────────────
    {
        "fips":         "48113",
        "name":         "Dallas County, TX",
        "state":        "TX",
        "arcgis_url":   (
            "https://www.dallascad.org/arcgis/rest/services/"
            "Parcels/Parcels/MapServer/0/query"
        ),
        "arcgis_fields": {
            "parcel_id":   "ACCOUNT_NUM",
            "owner":       "OWNER",
            "address_all": "SITUS_FULL",
            "legal":       "LEGAL_DESCR",
            "area":        "LAND_SQFT",
            "city":        "SITUS_CITY",
            "zipcode":     "SITUS_ZIP",
        },
        "portal_url":  "https://www.dallascounty.org/countyclerk",
        "portal_type": "unknown",
        "notes":       "Dallas County Appraisal District ArcGIS layer. Records portal varies.",
    },
    {
        "fips":         "48201",
        "name":         "Harris County, TX",
        "state":        "TX",
        "arcgis_url":   (
            "https://arcgis.hcad.org/arcgis/rest/services/"
            "HCAD/Parcels/MapServer/0/query"
        ),
        "arcgis_fields": {
            "parcel_id":   "acct",
            "owner":       "owner_name",
            "address_all": "site_addr_1",
            "city":        "site_addr_3",
            "legal":       "legal_desc",
        },
        "portal_url":  "https://www.cclerk.hctx.net/applications/websearch",
        "portal_type": "custom",
        "notes":       "Houston area. HCAD parcel service. County clerk has separate search portal.",
    },

    # ── Arizona ───────────────────────────────────────────────────────────────
    {
        "fips":         "04013",
        "name":         "Maricopa County, AZ",
        "state":        "AZ",
        "arcgis_url":   (
            "https://maps.maricopa.gov/arcgis/rest/services/"
            "Parcels/Parcels/MapServer/0/query"
        ),
        "arcgis_fields": {
            "parcel_id":   "APN",
            "owner":       "OWN_NAM",
            "address_all": "SITE_ADDR",
            "legal":       "LEGAL_1",
            "subdivision": "SUBDIV",
            "city":        "SITE_CITY",
            "zipcode":     "SITE_ZIP",
        },
        "portal_url":  "https://recorder.maricopa.gov/recdocdata",
        "portal_type": "custom",
        "notes":       "Phoenix metro. APN used as parcel ID. Maricopa Recorder for documents.",
    },
    {
        "fips":         "04019",
        "name":         "Pima County, AZ",
        "state":        "AZ",
        "arcgis_url":   (
            "https://giswebservices.pima.gov/giswebservices/rest/services/"
            "Parcels/MapServer/0/query"
        ),
        "arcgis_fields": {
            "parcel_id":   "ParcelCode",
            "owner":       "OwnerName",
            "address_all": "SitusAddress",
            "legal":       "LegalDescription",
            "subdivision": "SubdivisionName",
            "city":        "SitusCity",
            "zipcode":     "SitusZip",
        },
        "portal_url":  "https://recorder.pima.gov",
        "portal_type": "custom",
        "notes":       "Tucson area.",
    },
    {
        "fips":         "04021",
        "name":         "Pinal County, AZ",
        "state":        "AZ",
        "arcgis_url":   (
            "https://gis.pinalcountyaz.gov/server/rest/services/"
            "Parcels/PinalParcels/MapServer/0/query"
        ),
        "arcgis_fields": {
            "parcel_id":   "APN",
            "owner":       "OWNER",
            "address_all": "SITUS1",
            "legal":       "LEGAL",
            "subdivision": "SUBDIVISION",
        },
        "portal_url":  "https://recorder.pinalcountyaz.gov",
        "portal_type": "custom",
        "notes":       "Casa Grande / Queen Creek area.",
    },
]

# ── Index for fast lookup ─────────────────────────────────────────────────────

_BY_FIPS  = {c["fips"]: c for c in COUNTY_REGISTRY}
_BY_STATE = {}
for _c in COUNTY_REGISTRY:
    _BY_STATE.setdefault(_c["state"], []).append(_c)


def get_all_counties() -> list[dict]:
    """Return all registry entries (lightweight view — no heavy field maps)."""
    return [
        {
            "fips":        c["fips"],
            "name":        c["name"],
            "state":       c["state"],
            "portal_type": c["portal_type"],
            "has_portal":  bool(c.get("portal_url")),
            "notes":       c.get("notes", ""),
        }
        for c in COUNTY_REGISTRY
    ]


def get_county(fips: str) -> dict | None:
    """Return the full county entry by FIPS code, or None."""
    return _BY_FIPS.get(fips)


def search_counties(query: str) -> list[dict]:
    """Search by partial county name, state code, or FIPS.
    Returns lightweight list (name, fips, state, portal_type)."""
    q = query.strip().lower()
    if not q:
        return get_all_counties()
    results = []
    for c in COUNTY_REGISTRY:
        if (q in c["name"].lower()
                or q == c["state"].lower()
                or q in c["fips"]):
            results.append({
                "fips":        c["fips"],
                "name":        c["name"],
                "state":       c["state"],
                "portal_type": c["portal_type"],
                "has_portal":  bool(c.get("portal_url")),
                "notes":       c.get("notes", ""),
            })
    return results
