"""RD persisted-identity grammar, separate from every legacy neutral predicate.

Accepts complete normalized model_dump(mode='json') objects, not Pydantic's
input coercions/default omission. No migration, registration or execution here.
JSONB cannot retain duplicate keys or distinguish lexical 1e0 from 1; raw codec
parsing remains required before persistence. Dates/UUIDs/UTC timestamps must be
canonical. The frozen IANA 2026b ZoneInfo name inventory is embedded in SQL; it never
consults server timezone names. Tests compare the complete local inventory.
"""

import hashlib
import json
import re
from pathlib import Path

from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.research_runtime.engine_broker_contracts import (
    RDChatRequest,
    RDEmbeddingRequest,
)

SOURCE_PINS = {
    "packages/research_runtime/broker_contracts.py": "c818c50dc6bd3ccf6917a404d9d15e1761a1ed577e8892fd95321cb553a44659",
    "packages/rdagent_adapter/contracts.py": "6ae2b4b5490f36ff170401cdfee76af2e9492a57ccf9dccfa656a7abfdef690c",
    "packages/factors/contracts.py": "be87b1ccbfad092d469e69b4ed2d214cf7030b7d35033e9ce103b3c58e6e22f6",
    "packages/research_runtime/engine_broker_contracts.py": "505ca997e63fab715215821ea093079506ad8c306f91e11d642e73d3854886fa",
    "packages/research_runtime/budget_contracts.py": "8bfa31312c8b8116c262c50f55d1c5cdb443809dde210c7cd74f9d08479c0d51",
}

# 027/027c explicitly versioned models add opt-in controller selections.
# Keep the original pin visible; allow this exact reviewed successor source only
# while projecting the legacy schema below. Published migrations stay immutable.
SUCCESSOR_SOURCE_PINS = {
    (
        "packages/rdagent_adapter/contracts.py",
        "6ae2b4b5490f36ff170401cdfee76af2e9492a57ccf9dccfa656a7abfdef690c",
    ): "f34eadb8c73ef1bf5acb7ba33b56e924d469f307a3b472bccabf5fe621df0027",
}

SUCCESSOR_DEPENDENCY_PINS = {
    "packages/rdagent_adapter/controller_profile.py": "47110f9dda399444f4d78d1458401d08408ad007fb6b75da5b6af51e5797bdb0",
    "packages/rdagent_adapter/controller_limits.py": "8861a64a54bcf0d6692e7516622f6396d270765b70ad643f6b1d4b084cdf6475",
}

# Frozen system IANA inventory: /usr/share/zoneinfo/tzdata.zi, version 2026b.
TIMEZONE_SOURCE = {
    "version": "2026b",
    "source": "/usr/share/zoneinfo/tzdata.zi",
    "sha256": "d5c5a95d9af0dadb52a22bd3dab806462f7b5f95dc5924694256da6fe5366c5c",
}
TIMEZONE_NAMES = (
    "Africa/Abidjan",
    "Africa/Accra",
    "Africa/Addis_Ababa",
    "Africa/Algiers",
    "Africa/Asmara",
    "Africa/Asmera",
    "Africa/Bamako",
    "Africa/Bangui",
    "Africa/Banjul",
    "Africa/Bissau",
    "Africa/Blantyre",
    "Africa/Brazzaville",
    "Africa/Bujumbura",
    "Africa/Cairo",
    "Africa/Casablanca",
    "Africa/Ceuta",
    "Africa/Conakry",
    "Africa/Dakar",
    "Africa/Dar_es_Salaam",
    "Africa/Djibouti",
    "Africa/Douala",
    "Africa/El_Aaiun",
    "Africa/Freetown",
    "Africa/Gaborone",
    "Africa/Harare",
    "Africa/Johannesburg",
    "Africa/Juba",
    "Africa/Kampala",
    "Africa/Khartoum",
    "Africa/Kigali",
    "Africa/Kinshasa",
    "Africa/Lagos",
    "Africa/Libreville",
    "Africa/Lome",
    "Africa/Luanda",
    "Africa/Lubumbashi",
    "Africa/Lusaka",
    "Africa/Malabo",
    "Africa/Maputo",
    "Africa/Maseru",
    "Africa/Mbabane",
    "Africa/Mogadishu",
    "Africa/Monrovia",
    "Africa/Nairobi",
    "Africa/Ndjamena",
    "Africa/Niamey",
    "Africa/Nouakchott",
    "Africa/Ouagadougou",
    "Africa/Porto-Novo",
    "Africa/Sao_Tome",
    "Africa/Timbuktu",
    "Africa/Tripoli",
    "Africa/Tunis",
    "Africa/Windhoek",
    "America/Adak",
    "America/Anchorage",
    "America/Anguilla",
    "America/Antigua",
    "America/Araguaina",
    "America/Argentina/Buenos_Aires",
    "America/Argentina/Catamarca",
    "America/Argentina/ComodRivadavia",
    "America/Argentina/Cordoba",
    "America/Argentina/Jujuy",
    "America/Argentina/La_Rioja",
    "America/Argentina/Mendoza",
    "America/Argentina/Rio_Gallegos",
    "America/Argentina/Salta",
    "America/Argentina/San_Juan",
    "America/Argentina/San_Luis",
    "America/Argentina/Tucuman",
    "America/Argentina/Ushuaia",
    "America/Aruba",
    "America/Asuncion",
    "America/Atikokan",
    "America/Atka",
    "America/Bahia",
    "America/Bahia_Banderas",
    "America/Barbados",
    "America/Belem",
    "America/Belize",
    "America/Blanc-Sablon",
    "America/Boa_Vista",
    "America/Bogota",
    "America/Boise",
    "America/Buenos_Aires",
    "America/Cambridge_Bay",
    "America/Campo_Grande",
    "America/Cancun",
    "America/Caracas",
    "America/Catamarca",
    "America/Cayenne",
    "America/Cayman",
    "America/Chicago",
    "America/Chihuahua",
    "America/Ciudad_Juarez",
    "America/Coral_Harbour",
    "America/Cordoba",
    "America/Costa_Rica",
    "America/Coyhaique",
    "America/Creston",
    "America/Cuiaba",
    "America/Curacao",
    "America/Danmarkshavn",
    "America/Dawson",
    "America/Dawson_Creek",
    "America/Denver",
    "America/Detroit",
    "America/Dominica",
    "America/Edmonton",
    "America/Eirunepe",
    "America/El_Salvador",
    "America/Ensenada",
    "America/Fort_Nelson",
    "America/Fort_Wayne",
    "America/Fortaleza",
    "America/Glace_Bay",
    "America/Godthab",
    "America/Goose_Bay",
    "America/Grand_Turk",
    "America/Grenada",
    "America/Guadeloupe",
    "America/Guatemala",
    "America/Guayaquil",
    "America/Guyana",
    "America/Halifax",
    "America/Havana",
    "America/Hermosillo",
    "America/Indiana/Indianapolis",
    "America/Indiana/Knox",
    "America/Indiana/Marengo",
    "America/Indiana/Petersburg",
    "America/Indiana/Tell_City",
    "America/Indiana/Vevay",
    "America/Indiana/Vincennes",
    "America/Indiana/Winamac",
    "America/Indianapolis",
    "America/Inuvik",
    "America/Iqaluit",
    "America/Jamaica",
    "America/Jujuy",
    "America/Juneau",
    "America/Kentucky/Louisville",
    "America/Kentucky/Monticello",
    "America/Knox_IN",
    "America/Kralendijk",
    "America/La_Paz",
    "America/Lima",
    "America/Los_Angeles",
    "America/Louisville",
    "America/Lower_Princes",
    "America/Maceio",
    "America/Managua",
    "America/Manaus",
    "America/Marigot",
    "America/Martinique",
    "America/Matamoros",
    "America/Mazatlan",
    "America/Mendoza",
    "America/Menominee",
    "America/Merida",
    "America/Metlakatla",
    "America/Mexico_City",
    "America/Miquelon",
    "America/Moncton",
    "America/Monterrey",
    "America/Montevideo",
    "America/Montreal",
    "America/Montserrat",
    "America/Nassau",
    "America/New_York",
    "America/Nipigon",
    "America/Nome",
    "America/Noronha",
    "America/North_Dakota/Beulah",
    "America/North_Dakota/Center",
    "America/North_Dakota/New_Salem",
    "America/Nuuk",
    "America/Ojinaga",
    "America/Panama",
    "America/Pangnirtung",
    "America/Paramaribo",
    "America/Phoenix",
    "America/Port-au-Prince",
    "America/Port_of_Spain",
    "America/Porto_Acre",
    "America/Porto_Velho",
    "America/Puerto_Rico",
    "America/Punta_Arenas",
    "America/Rainy_River",
    "America/Rankin_Inlet",
    "America/Recife",
    "America/Regina",
    "America/Resolute",
    "America/Rio_Branco",
    "America/Rosario",
    "America/Santa_Isabel",
    "America/Santarem",
    "America/Santiago",
    "America/Santo_Domingo",
    "America/Sao_Paulo",
    "America/Scoresbysund",
    "America/Shiprock",
    "America/Sitka",
    "America/St_Barthelemy",
    "America/St_Johns",
    "America/St_Kitts",
    "America/St_Lucia",
    "America/St_Thomas",
    "America/St_Vincent",
    "America/Swift_Current",
    "America/Tegucigalpa",
    "America/Thule",
    "America/Thunder_Bay",
    "America/Tijuana",
    "America/Toronto",
    "America/Tortola",
    "America/Vancouver",
    "America/Virgin",
    "America/Whitehorse",
    "America/Winnipeg",
    "America/Yakutat",
    "America/Yellowknife",
    "Antarctica/Casey",
    "Antarctica/Davis",
    "Antarctica/DumontDUrville",
    "Antarctica/Macquarie",
    "Antarctica/Mawson",
    "Antarctica/McMurdo",
    "Antarctica/Palmer",
    "Antarctica/Rothera",
    "Antarctica/South_Pole",
    "Antarctica/Syowa",
    "Antarctica/Troll",
    "Antarctica/Vostok",
    "Arctic/Longyearbyen",
    "Asia/Aden",
    "Asia/Almaty",
    "Asia/Amman",
    "Asia/Anadyr",
    "Asia/Aqtau",
    "Asia/Aqtobe",
    "Asia/Ashgabat",
    "Asia/Ashkhabad",
    "Asia/Atyrau",
    "Asia/Baghdad",
    "Asia/Bahrain",
    "Asia/Baku",
    "Asia/Bangkok",
    "Asia/Barnaul",
    "Asia/Beirut",
    "Asia/Bishkek",
    "Asia/Brunei",
    "Asia/Calcutta",
    "Asia/Chita",
    "Asia/Choibalsan",
    "Asia/Chongqing",
    "Asia/Chungking",
    "Asia/Colombo",
    "Asia/Dacca",
    "Asia/Damascus",
    "Asia/Dhaka",
    "Asia/Dili",
    "Asia/Dubai",
    "Asia/Dushanbe",
    "Asia/Famagusta",
    "Asia/Gaza",
    "Asia/Harbin",
    "Asia/Hebron",
    "Asia/Ho_Chi_Minh",
    "Asia/Hong_Kong",
    "Asia/Hovd",
    "Asia/Irkutsk",
    "Asia/Istanbul",
    "Asia/Jakarta",
    "Asia/Jayapura",
    "Asia/Jerusalem",
    "Asia/Kabul",
    "Asia/Kamchatka",
    "Asia/Karachi",
    "Asia/Kashgar",
    "Asia/Kathmandu",
    "Asia/Katmandu",
    "Asia/Khandyga",
    "Asia/Kolkata",
    "Asia/Krasnoyarsk",
    "Asia/Kuala_Lumpur",
    "Asia/Kuching",
    "Asia/Kuwait",
    "Asia/Macao",
    "Asia/Macau",
    "Asia/Magadan",
    "Asia/Makassar",
    "Asia/Manila",
    "Asia/Muscat",
    "Asia/Nicosia",
    "Asia/Novokuznetsk",
    "Asia/Novosibirsk",
    "Asia/Omsk",
    "Asia/Oral",
    "Asia/Phnom_Penh",
    "Asia/Pontianak",
    "Asia/Pyongyang",
    "Asia/Qatar",
    "Asia/Qostanay",
    "Asia/Qyzylorda",
    "Asia/Rangoon",
    "Asia/Riyadh",
    "Asia/Saigon",
    "Asia/Sakhalin",
    "Asia/Samarkand",
    "Asia/Seoul",
    "Asia/Shanghai",
    "Asia/Singapore",
    "Asia/Srednekolymsk",
    "Asia/Taipei",
    "Asia/Tashkent",
    "Asia/Tbilisi",
    "Asia/Tehran",
    "Asia/Tel_Aviv",
    "Asia/Thimbu",
    "Asia/Thimphu",
    "Asia/Tokyo",
    "Asia/Tomsk",
    "Asia/Ujung_Pandang",
    "Asia/Ulaanbaatar",
    "Asia/Ulan_Bator",
    "Asia/Urumqi",
    "Asia/Ust-Nera",
    "Asia/Vientiane",
    "Asia/Vladivostok",
    "Asia/Yakutsk",
    "Asia/Yangon",
    "Asia/Yekaterinburg",
    "Asia/Yerevan",
    "Atlantic/Azores",
    "Atlantic/Bermuda",
    "Atlantic/Canary",
    "Atlantic/Cape_Verde",
    "Atlantic/Faeroe",
    "Atlantic/Faroe",
    "Atlantic/Jan_Mayen",
    "Atlantic/Madeira",
    "Atlantic/Reykjavik",
    "Atlantic/South_Georgia",
    "Atlantic/St_Helena",
    "Atlantic/Stanley",
    "Australia/ACT",
    "Australia/Adelaide",
    "Australia/Brisbane",
    "Australia/Broken_Hill",
    "Australia/Canberra",
    "Australia/Currie",
    "Australia/Darwin",
    "Australia/Eucla",
    "Australia/Hobart",
    "Australia/LHI",
    "Australia/Lindeman",
    "Australia/Lord_Howe",
    "Australia/Melbourne",
    "Australia/NSW",
    "Australia/North",
    "Australia/Perth",
    "Australia/Queensland",
    "Australia/South",
    "Australia/Sydney",
    "Australia/Tasmania",
    "Australia/Victoria",
    "Australia/West",
    "Australia/Yancowinna",
    "Brazil/Acre",
    "Brazil/DeNoronha",
    "Brazil/East",
    "Brazil/West",
    "CET",
    "CST6CDT",
    "Canada/Atlantic",
    "Canada/Central",
    "Canada/Eastern",
    "Canada/Mountain",
    "Canada/Newfoundland",
    "Canada/Pacific",
    "Canada/Saskatchewan",
    "Canada/Yukon",
    "Chile/Continental",
    "Chile/EasterIsland",
    "Cuba",
    "EET",
    "EST",
    "EST5EDT",
    "Egypt",
    "Eire",
    "Etc/GMT",
    "Etc/GMT+0",
    "Etc/GMT+1",
    "Etc/GMT+10",
    "Etc/GMT+11",
    "Etc/GMT+12",
    "Etc/GMT+2",
    "Etc/GMT+3",
    "Etc/GMT+4",
    "Etc/GMT+5",
    "Etc/GMT+6",
    "Etc/GMT+7",
    "Etc/GMT+8",
    "Etc/GMT+9",
    "Etc/GMT-0",
    "Etc/GMT-1",
    "Etc/GMT-10",
    "Etc/GMT-11",
    "Etc/GMT-12",
    "Etc/GMT-13",
    "Etc/GMT-14",
    "Etc/GMT-2",
    "Etc/GMT-3",
    "Etc/GMT-4",
    "Etc/GMT-5",
    "Etc/GMT-6",
    "Etc/GMT-7",
    "Etc/GMT-8",
    "Etc/GMT-9",
    "Etc/GMT0",
    "Etc/Greenwich",
    "Etc/UCT",
    "Etc/UTC",
    "Etc/Universal",
    "Etc/Zulu",
    "Europe/Amsterdam",
    "Europe/Andorra",
    "Europe/Astrakhan",
    "Europe/Athens",
    "Europe/Belfast",
    "Europe/Belgrade",
    "Europe/Berlin",
    "Europe/Bratislava",
    "Europe/Brussels",
    "Europe/Bucharest",
    "Europe/Budapest",
    "Europe/Busingen",
    "Europe/Chisinau",
    "Europe/Copenhagen",
    "Europe/Dublin",
    "Europe/Gibraltar",
    "Europe/Guernsey",
    "Europe/Helsinki",
    "Europe/Isle_of_Man",
    "Europe/Istanbul",
    "Europe/Jersey",
    "Europe/Kaliningrad",
    "Europe/Kiev",
    "Europe/Kirov",
    "Europe/Kyiv",
    "Europe/Lisbon",
    "Europe/Ljubljana",
    "Europe/London",
    "Europe/Luxembourg",
    "Europe/Madrid",
    "Europe/Malta",
    "Europe/Mariehamn",
    "Europe/Minsk",
    "Europe/Monaco",
    "Europe/Moscow",
    "Europe/Nicosia",
    "Europe/Oslo",
    "Europe/Paris",
    "Europe/Podgorica",
    "Europe/Prague",
    "Europe/Riga",
    "Europe/Rome",
    "Europe/Samara",
    "Europe/San_Marino",
    "Europe/Sarajevo",
    "Europe/Saratov",
    "Europe/Simferopol",
    "Europe/Skopje",
    "Europe/Sofia",
    "Europe/Stockholm",
    "Europe/Tallinn",
    "Europe/Tirane",
    "Europe/Tiraspol",
    "Europe/Ulyanovsk",
    "Europe/Uzhgorod",
    "Europe/Vaduz",
    "Europe/Vatican",
    "Europe/Vienna",
    "Europe/Vilnius",
    "Europe/Volgograd",
    "Europe/Warsaw",
    "Europe/Zagreb",
    "Europe/Zaporozhye",
    "Europe/Zurich",
    "Factory",
    "GB",
    "GB-Eire",
    "GMT",
    "GMT+0",
    "GMT-0",
    "GMT0",
    "Greenwich",
    "HST",
    "Hongkong",
    "Iceland",
    "Indian/Antananarivo",
    "Indian/Chagos",
    "Indian/Christmas",
    "Indian/Cocos",
    "Indian/Comoro",
    "Indian/Kerguelen",
    "Indian/Mahe",
    "Indian/Maldives",
    "Indian/Mauritius",
    "Indian/Mayotte",
    "Indian/Reunion",
    "Iran",
    "Israel",
    "Jamaica",
    "Japan",
    "Kwajalein",
    "Libya",
    "MET",
    "MST",
    "MST7MDT",
    "Mexico/BajaNorte",
    "Mexico/BajaSur",
    "Mexico/General",
    "NZ",
    "NZ-CHAT",
    "Navajo",
    "PRC",
    "PST8PDT",
    "Pacific/Apia",
    "Pacific/Auckland",
    "Pacific/Bougainville",
    "Pacific/Chatham",
    "Pacific/Chuuk",
    "Pacific/Easter",
    "Pacific/Efate",
    "Pacific/Enderbury",
    "Pacific/Fakaofo",
    "Pacific/Fiji",
    "Pacific/Funafuti",
    "Pacific/Galapagos",
    "Pacific/Gambier",
    "Pacific/Guadalcanal",
    "Pacific/Guam",
    "Pacific/Honolulu",
    "Pacific/Johnston",
    "Pacific/Kanton",
    "Pacific/Kiritimati",
    "Pacific/Kosrae",
    "Pacific/Kwajalein",
    "Pacific/Majuro",
    "Pacific/Marquesas",
    "Pacific/Midway",
    "Pacific/Nauru",
    "Pacific/Niue",
    "Pacific/Norfolk",
    "Pacific/Noumea",
    "Pacific/Pago_Pago",
    "Pacific/Palau",
    "Pacific/Pitcairn",
    "Pacific/Pohnpei",
    "Pacific/Ponape",
    "Pacific/Port_Moresby",
    "Pacific/Rarotonga",
    "Pacific/Saipan",
    "Pacific/Samoa",
    "Pacific/Tahiti",
    "Pacific/Tarawa",
    "Pacific/Tongatapu",
    "Pacific/Truk",
    "Pacific/Wake",
    "Pacific/Wallis",
    "Pacific/Yap",
    "Poland",
    "Portugal",
    "ROC",
    "ROK",
    "Singapore",
    "Turkey",
    "UCT",
    "US/Alaska",
    "US/Aleutian",
    "US/Arizona",
    "US/Central",
    "US/East-Indiana",
    "US/Eastern",
    "US/Hawaii",
    "US/Indiana-Starke",
    "US/Michigan",
    "US/Mountain",
    "US/Pacific",
    "US/Samoa",
    "UTC",
    "Universal",
    "W-SU",
    "WET",
    "Zulu",
    "localtime",
)


def _quote(value):
    return "'" + value.replace("'", "''") + "'"


def schemas():
    """Exact supported schema subset; any new keyword fails render for review."""
    values = {
        name: cls.model_json_schema()
        for name, cls in (
            ("config", FactorResearchConfig),
            ("chat", RDChatRequest),
            ("embedding", RDEmbeddingRequest),
        )
    }
    # 018/026 grammars predate explicit controller selection;027 wraps these
    # frozen predicates instead of rewriting their historical accepted inputs.
    values["config"]["properties"].pop("controller_profile", None)
    allowed = {
        "$defs",
        "$ref",
        "additionalProperties",
        "properties",
        "required",
        "title",
        "type",
        "default",
        "const",
        "enum",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "items",
        "minItems",
        "maxItems",
        "anyOf",
        "oneOf",
        "discriminator",
    }

    def check(node):
        if set(node) - allowed:
            raise ValueError("Unreviewed JSON Schema keyword")
        if (
            node.get("type") == "object"
            and node.get("additionalProperties") is not False
        ):
            raise ValueError("Open object grammar forbidden")
        for key in ("$defs", "properties"):
            for child in node.get(key, {}).values():
                check(child)
        for key in ("anyOf", "oneOf"):
            for child in node.get(key, []):
                check(child)
        if "items" in node:
            check(node["items"])

    for value in values.values():
        check(value)
    return values


def render_sql(schema="public"):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
        raise ValueError("Unsafe schema")
    root = Path(__file__).resolve().parents[2]
    for path, sha in SOURCE_PINS.items():
        source = root / path
        if any(p.is_symlink() for p in (source, *source.parents)):
            raise ValueError("Symlink contract source")
        if hashlib.sha256(source.read_bytes()).hexdigest() not in (
            sha,
            SUCCESSOR_SOURCE_PINS.get((path, sha)),
        ):
            raise ValueError("Contract source drift: " + path)
    for path, expected in SUCCESSOR_DEPENDENCY_PINS.items():
        source = root / path
        if (
            any(p.is_symlink() for p in (source, *source.parents))
            or hashlib.sha256(source.read_bytes()).hexdigest() != expected
        ):
            raise ValueError("Controller selection dependency drift")
    definitions = schemas()
    zones = list(TIMEZONE_NAMES)
    if not zones or "UTC" not in zones:
        raise ValueError("ZoneInfo inventory unavailable")
    sql = _SQL.replace("@S@", schema)
    sql = sql.replace("@ZONES@", _quote(json.dumps(zones)) + "::jsonb")
    for name, value in definitions.items():
        sql = sql.replace(
            "@" + name.upper() + "@", _quote(json.dumps(value)) + "::jsonb"
        )
    return (
        "-- Frozen ZoneInfo names SHA256: "
        + hashlib.sha256(json.dumps(zones, separators=(",", ":")).encode()).hexdigest()
        + "\n"
        + sql
    )


_SQL = r"""
-- Canonical encoded byte length, no dependence on JSONB pretty-print whitespace.
CREATE OR REPLACE FUNCTION @S@.mineagent_rd_json_bytes(v jsonb, ascii_only boolean, depth integer DEFAULT 0)
RETURNS bigint LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE n bigint := 0; e record; c text; p integer; t text; k integer;
BEGIN
 IF v IS NULL THEN RETURN NULL; END IF;
 IF depth>24 THEN RETURN 1048577; END IF;
 CASE jsonb_typeof(v)
 WHEN 'object' THEN
   n:=2; k:=0;
   FOR e IN SELECT key,value FROM jsonb_each(v) LOOP
     n:=n + @S@.mineagent_rd_json_bytes(to_jsonb(e.key),ascii_only,depth+1)
          + @S@.mineagent_rd_json_bytes(e.value,ascii_only,depth+1)+1;
     k:=k+1;
   END LOOP;
   RETURN n+greatest(k-1,0);
 WHEN 'array' THEN
   n:=2; k:=0;
   FOR e IN SELECT value FROM jsonb_array_elements(v) LOOP
     n:=n+@S@.mineagent_rd_json_bytes(e.value,ascii_only,depth+1); k:=k+1;
   END LOOP;
   RETURN n+greatest(k-1,0);
 WHEN 'string' THEN
   n:=2; t:=v#>>'{}';
   IF current_setting('server_encoding')='UTF8' AND octet_length(t)<=1048576 AND octet_length(t)=char_length(t) AND position(chr(127) in t)=0 THEN
     RETURN octet_length(pg_catalog.to_json(t)::text);
   END IF;
   FOR k IN 1..char_length(t) LOOP
     c:=substr(t,k,1); p:=ascii(c);
     n:=n+CASE WHEN p IN (8,9,10,12,13,34,92) THEN 2
       WHEN p<32 THEN 6 WHEN ascii_only AND p>65535 THEN 12
       WHEN ascii_only AND p>=127 THEN 6 ELSE octet_length(c) END;
   END LOOP;
   RETURN n;
 ELSE RETURN octet_length(v::text);
 END CASE;
END $$;

CREATE OR REPLACE FUNCTION @S@.mineagent_rd_schema(v jsonb, rule jsonb, defs jsonb, depth integer DEFAULT 0)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE e record; r jsonb; t text; s text; kind text; title text; n integer; previous text; x text;
BEGIN
 IF v IS NULL OR depth>24 THEN RETURN false; END IF;
 IF rule ? '$ref' THEN
   RETURN @S@.mineagent_rd_schema(v,defs->split_part(rule->>'$ref','/',3),defs,depth+1);
 END IF;
 IF rule ? 'anyOf' OR rule ? 'oneOf' THEN
   n:=0;
   FOR r IN SELECT value FROM jsonb_array_elements(coalesce(rule->'anyOf',rule->'oneOf')) LOOP
     IF @S@.mineagent_rd_schema(v,r,defs,depth+1) THEN n:=n+1; END IF;
   END LOOP;
   RETURN CASE WHEN rule ? 'oneOf' THEN n=1 ELSE n>0 END;
 END IF;
 kind:=rule->>'type'; title:=rule->>'title';
 IF kind IS NULL THEN RETURN false; END IF;
 IF jsonb_typeof(v) IS DISTINCT FROM (CASE WHEN kind='integer' THEN 'number' ELSE kind END) THEN RETURN false; END IF;
 IF rule ? 'const' AND v IS DISTINCT FROM rule->'const' THEN RETURN false; END IF;
 IF rule ? 'enum' AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(rule->'enum') a WHERE a.value=v) THEN RETURN false; END IF;
 CASE kind
 WHEN 'integer' THEN
   IF v::text !~ '^(0|[1-9][0-9]*)$' THEN RETURN false; END IF;
   IF rule ? 'minimum' AND (v::text)::numeric<(rule->>'minimum')::numeric THEN RETURN false; END IF;
   IF rule ? 'maximum' AND (v::text)::numeric>(rule->>'maximum')::numeric THEN RETURN false; END IF;
 WHEN 'string' THEN
   t:=v#>>'{}'; n:=char_length(t);
   IF rule ? 'minLength' AND n<(rule->>'minLength')::integer THEN RETURN false; END IF;
   IF rule ? 'maxLength' AND n>(rule->>'maxLength')::integer THEN RETURN false; END IF;
   s:=rule->>'pattern';
   IF s=E'^[^\\x00-\\x1f\\x7f]+$' THEN
     IF EXISTS(SELECT 1 FROM generate_series(1,n) i WHERE ascii(substr(t,i,1))<32 OR ascii(substr(t,i,1))=127) THEN RETURN false; END IF;
   ELSIF s=E'^[^\\x00]+$' THEN NULL; -- PostgreSQL text/jsonb cannot represent NUL.
   ELSIF s IS NOT NULL AND t !~ s THEN RETURN false;
   END IF;
   CASE rule->>'format'
   WHEN 'uuid' THEN
     IF t !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN RETURN false; END IF;
   WHEN 'date' THEN
     IF t !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' OR left(t,4)='0000' THEN RETURN false; END IF;
     IF to_char(t::date,'YYYY-MM-DD')<>t THEN RETURN false; END IF;
   WHEN 'date-time' THEN
     IF t !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{6})?Z$'
       OR left(t,4)='0000' THEN RETURN false; END IF;
     IF right(t,8)='.000000Z' THEN RETURN false; END IF;
     IF to_char(t::timestamptz AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS')<>left(t,19) THEN RETURN false; END IF;
   ELSE NULL;
   END CASE;
 WHEN 'array' THEN
   n:=jsonb_array_length(v);
   IF rule ? 'minItems' AND n<(rule->>'minItems')::integer THEN RETURN false; END IF;
   IF rule ? 'maxItems' AND n>(rule->>'maxItems')::integer THEN RETURN false; END IF;
   FOR r IN SELECT value FROM jsonb_array_elements(v) LOOP
     IF NOT @S@.mineagent_rd_schema(r,rule->'items',defs,depth+1) THEN RETURN false; END IF;
   END LOOP;
 WHEN 'object' THEN
   IF (SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(v) key)
     IS DISTINCT FROM (SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(rule->'properties') key) THEN RETURN false; END IF;
   FOR e IN SELECT key,value FROM jsonb_each(rule->'properties') LOOP
     IF NOT @S@.mineagent_rd_schema(v->e.key,e.value,defs,depth+1) THEN RETURN false; END IF;
   END LOOP;
 WHEN 'null' THEN NULL;
 ELSE RETURN false;
 END CASE;
 -- Model validators not expressible in the generated structural schema.
 CASE title
 WHEN 'DateWindow' THEN
   IF (v->>'start')::date >= (v->>'end')::date THEN RETURN false; END IF;
 WHEN 'FittedPreprocessing' THEN
   IF (v->>'available_at')::timestamptz < (v->>'last_input_at')::timestamptz THEN RETURN false; END IF;
 WHEN 'CalculationUniverse' THEN
   previous:=NULL;
   FOR r IN SELECT value FROM jsonb_array_elements(v->'listing_ids') LOOP
     x:=r#>>'{}';
     IF previous IS NOT NULL AND previous COLLATE "C">=x COLLATE "C" THEN RETURN false; END IF;
     previous:=x;
   END LOOP;
 WHEN 'RuntimePins' THEN
   IF (SELECT jsonb_agg(value->'role' ORDER BY ord) FROM jsonb_array_elements(v->'dependency_locks') WITH ORDINALITY a(value,ord))
      <> '["analysis","candidate","normalizer","orchestrator"]'::jsonb THEN RETURN false; END IF;
 WHEN 'FrozenResearchInput' THEN
   IF NOT (@ZONES@ ? (v->>'market_timezone')) THEN RETURN false; END IF;
   IF (SELECT count(DISTINCT value->>'name') FROM jsonb_array_elements(v->'input_fields'))<>jsonb_array_length(v->'input_fields') THEN RETURN false; END IF;
   FOR r IN SELECT value FROM jsonb_array_elements(v->'input_fields') LOOP
     IF r->>'name' NOT IN ('open','high','low','close','volume') THEN RETURN false; END IF;
     IF r->>'price_semantics'<>'not_price' AND r->>'price_semantics'<>v#>>'{dataset,price_semantics}' THEN RETURN false; END IF;
   END LOOP;
 WHEN 'FactorResearchConfig' THEN
   IF (v#>>'{budget,calls}')::numeric<1 OR (v#>>'{budget,mutation_slots}')::numeric<(v->>'max_iterations')::integer THEN RETURN false; END IF;
   FOREACH s IN ARRAY ARRAY['evaluator_executions','wall_milliseconds','storage_bytes','process_slots'] LOOP
     IF (v->'budget'->>s)::numeric<1 THEN RETURN false; END IF;
   END LOOP;
   IF jsonb_array_length(v#>'{input,calculation_universe,listing_ids}')>(v->>'max_output_rows_per_cutoff')::integer THEN RETURN false; END IF;
 WHEN 'RDEmbeddingRequest' THEN
   FOR r IN SELECT value FROM jsonb_array_elements(v->'inputs') LOOP
     IF char_length(r#>>'{}') NOT BETWEEN 1 AND 8192 THEN RETURN false; END IF;
   END LOOP;
 ELSE NULL;
 END CASE;
 RETURN true;
EXCEPTION WHEN data_exception THEN RETURN false;
END $$;

CREATE OR REPLACE FUNCTION @S@.mineagent_rd_source_config(v jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT coalesce(@S@.mineagent_rd_schema(v,@CONFIG@,(@CONFIG@)->'$defs')
   AND @S@.mineagent_rd_json_bytes(v,true)<=1048576,false)
$$;
CREATE OR REPLACE FUNCTION @S@.mineagent_rd_source_request(v jsonb,protocol text)
RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT coalesce(CASE protocol
 WHEN 'mineagent.rdagent-chat.v1' THEN @S@.mineagent_rd_schema(v,@CHAT@,(@CHAT@)->'$defs')
 WHEN 'mineagent.rdagent-embedding.v1' THEN @S@.mineagent_rd_schema(v,@EMBEDDING@,(@EMBEDDING@)->'$defs')
 ELSE false END AND @S@.mineagent_rd_json_bytes(v,false)<=262144,false)
$$;
CREATE OR REPLACE FUNCTION @S@.mineagent_rd_source_context(v jsonb)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
 IF jsonb_typeof(v) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
 IF (SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(v) key)
   IS DISTINCT FROM ARRAY['input_sha256','logical_step','schema_version','spec_sha256','task_index'] THEN RETURN false; END IF;
 RETURN coalesce(v->>'schema_version'='mineagent.rdagent-context.v1'
 AND jsonb_typeof(v->'input_sha256')='string' AND v->>'input_sha256' ~ '^[a-f0-9]{64}$'
 AND jsonb_typeof(v->'spec_sha256')='string' AND v->>'spec_sha256' ~ '^[a-f0-9]{64}$'
 AND jsonb_typeof(v->'logical_step')='number' AND (v->'logical_step')::text ~ '^[0-7]$'
 AND jsonb_typeof(v->'task_index')='number' AND (v->'task_index')::text='0',false);
END $$;
"""
