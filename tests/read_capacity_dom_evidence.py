"""Read the real headless Chromium DOM evidence without a browser dependency."""
import json
import sys
from html.parser import HTMLParser
from pathlib import Path


class Evidence(HTMLParser):
    active = False
    result = ""

    def handle_starttag(self, tag, attrs):
        if tag == "pre" and dict(attrs).get("id") == "evidence":
            self.active = True

    def handle_endtag(self, tag):
        if tag == "pre":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.result += data


path = Path(sys.argv[1])
parser = Evidence()
parser.feed(path.read_text(encoding="utf-8"))
result = json.loads(parser.result)
path.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
assert result["status"] == "passed"
