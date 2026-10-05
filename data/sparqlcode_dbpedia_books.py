import os
import string
import time
from xml.etree import ElementTree as ET
from xml.dom import minidom
from SPARQLWrapper import SPARQLWrapper, JSON

# 1. Endpunkt konfigurieren
sparql = SPARQLWrapper("https://dbpedia.org/sparql")
sparql.setTimeout(60)

# Direkt in den Unterordner 'data' deines aktuellen VS Code Projekts speichern
base_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in locals() else os.getcwd()
output_dir = os.path.join(base_dir, "data") if not base_dir.endswith("data") else base_dir
os.makedirs(output_dir, exist_ok=True)

# Neuer Dateiname: dbpedia_book_data.xml
output_file = os.path.join(output_dir, "dbpedia_book_data.xml")

# Wurzel-Element für das XML anlegen
root = ET.Element("books")

letters = list(string.ascii_uppercase) + [str(i) for i in range(10)]
total_saved = 0

print("Starte Datenabruf und XML-Erstellung...")

for char in letters:
    query = f"""
    PREFIX dbo: <http://dbpedia.org/ontology/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT 
        (SAMPLE(?t) AS ?title)
        (GROUP_CONCAT(DISTINCT ?isbnVal; separator=";;") AS ?isbns)
        (GROUP_CONCAT(DISTINCT ?authorVal; separator=";;") AS ?authors)
        (SAMPLE(?pubVal) AS ?publisher)
        (SAMPLE(?yearVal) AS ?year)
        (SAMPLE(?pagesVal) AS ?pages)
        (SAMPLE(?langVal) AS ?language)
        (GROUP_CONCAT(DISTINCT ?genreVal; separator=";;") AS ?genres)
    WHERE {{
        ?book a dbo:Book ;
              rdfs:label ?t ;
              dbo:isbn ?isbnVal .
        
        OPTIONAL {{
            ?book dbo:author ?a .
            ?a rdfs:label ?authorVal .
            FILTER (lang(?authorVal) = 'en' || lang(?authorVal) = '')
        }}
        
        OPTIONAL {{
            ?book dbo:publisher ?p .
            ?p rdfs:label ?pubVal .
            FILTER (lang(?pubVal) = 'en' || lang(?pubVal) = '')
        }}

        OPTIONAL {{ ?book dbo:numberOfPages ?pagesVal . }}
        
        OPTIONAL {{
            ?book dbo:releaseDate ?date .
            BIND(SUBSTR(STR(?date), 1, 4) AS ?yearVal)
        }}

        OPTIONAL {{
            ?book dbo:language ?l .
            ?l rdfs:label ?langVal .
            FILTER (lang(?langVal) = 'en')
        }}

        OPTIONAL {{
            ?book dbo:literaryGenre ?g .
            ?g rdfs:label ?genreVal .
            FILTER (lang(?genreVal) = 'en' || lang(?genreVal) = '')
        }}

        FILTER (lang(?t) = 'en')
        FILTER (STRSTARTS(UCASE(STR(?t)), "{char}"))
    }}
    GROUP BY ?book
    LIMIT 2000
    """

    sparql.setQuery(query)
    sparql.setReturnFormat(JSON)

    attempts = 0
    success = False

    while not success and attempts < 4:
        try:
            attempts += 1
            results = sparql.query().convert()
            bindings = results["results"]["bindings"]
            success = True

            if bindings:
                for row in bindings:
                    book_el = ET.SubElement(root, "book")

                    # Einfache Attribute (ohne ratings/reviews)
                    ET.SubElement(book_el, "title").text = row.get("title", {}).get("value", "")
                    ET.SubElement(book_el, "publisher").text = row.get("publisher", {}).get("value", "")
                    ET.SubElement(book_el, "year").text = row.get("year", {}).get("value", "")
                    ET.SubElement(book_el, "pages").text = row.get("pages", {}).get("value", "")
                    ET.SubElement(book_el, "language").text = row.get("language", {}).get("value", "")

                    # Listen-Attribut: ISBNs
                    isbns_el = ET.SubElement(book_el, "isbns")
                    raw_isbns = row.get("isbns", {}).get("value", "")
                    if raw_isbns:
                        for val in raw_isbns.split(";;"):
                            ET.SubElement(isbns_el, "isbn").text = val.strip()

                    # Listen-Attribut: Autoren
                    authors_el = ET.SubElement(book_el, "authors")
                    raw_authors = row.get("authors", {}).get("value", "")
                    if raw_authors:
                        for val in raw_authors.split(";;"):
                            ET.SubElement(authors_el, "author").text = val.strip()

                    # Listen-Attribut: Genres
                    genres_el = ET.SubElement(book_el, "genres")
                    raw_genres = row.get("genres", {}).get("value", "")
                    if raw_genres:
                        for val in raw_genres.split(";;"):
                            ET.SubElement(genres_el, "genre").text = val.strip()

                total_saved += len(bindings)
                print(f"Buchstabe/Ziffer '{char}': +{len(bindings)} Bücher (Gesamt: {total_saved})")
            else:
                print(f"Buchstabe/Ziffer '{char}': Keine Einträge")

            time.sleep(0.3)
        except Exception as e:
            print(f"  Timeout bei '{char}' (Versuch {attempts}/4). Warte 3 Sekunden...")
            time.sleep(3)

# 2. Formatiertes XML sauber auf die Festplatte schreiben
print("\nFormatiere und speichere XML...")
xml_string = ET.tostring(root, encoding="utf-8")
parsed_dom = minidom.parseString(xml_string)
pretty_xml = parsed_dom.toprettyxml(indent="  ", encoding="utf-8")

with open(output_file, "wb") as f:
    f.write(pretty_xml)

print(f"Fertig! Datei erfolgreich abgelegt unter:\n{output_file}")