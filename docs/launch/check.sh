#!/usr/bin/env bash
# Daily launch check: public numbers only. Run from anywhere: bash docs/launch/check.sh
set -u
REPO=shangliy/kvleak
HN_ITEM=50176591
PR=ComposioHQ/awesome-claude-skills/pull/2356
UA="kvleak-launch-check/1.0"

echo "== kvleak launch check · $(date -u +%Y-%m-%dT%H:%MZ)"

echo "-- GitHub"
gh api "repos/$REPO" --jq '"stars \(.stargazers_count) · forks \(.forks_count) · open issues \(.open_issues_count)"'
gh api "repos/$REPO/traffic/views" --jq '"views 14d: \(.count) (\(.uniques) unique) · yesterday: \(.views[-1].count // 0)"' 2>/dev/null
gh api "repos/$REPO/traffic/clones" --jq '"clones 14d: \(.count) (\(.uniques) unique)"' 2>/dev/null
gh api "repos/$REPO/traffic/popular/referrers" --jq 'if length==0 then "referrers: none" else "referrers: " + (map("\(.referrer) \(.count)") | join(", ")) end' 2>/dev/null
gh api "repos/$REPO/stargazers" -H "Accept: application/vnd.github.star+json" --jq '.[-5:] | map("\(.starred_at[:10]) \(.user.login)") | "latest stars: " + join(", ")' 2>/dev/null
gh api "repos/$REPO/issues?state=all&sort=updated&per_page=5" --jq '.[] | "issue/PR #\(.number) \(.state) · \(.comments) comments · \(.title[:60])"'

echo "-- PyPI (pypistats lags a day; a brand-new package's first days are mostly mirrors)"
curl -s -A "$UA" "https://pypistats.org/api/packages/kvleak/recent" | python3 -c 'import sys,json; d=json.load(sys.stdin)["data"]; print("downloads: day {} · week {} · month {}".format(d["last_day"], d["last_week"], d["last_month"]))' 2>/dev/null
# Humans show up as macOS/Windows; "null" has no pip user agent (scanners, crawlers); Linux+py3.9 is mostly scanners too.
curl -s -A "$UA" "https://pypistats.org/api/packages/kvleak/system?mirrors=false" | python3 -c '
import sys,json,collections
rows=json.load(sys.stdin)["data"]; by=collections.defaultdict(dict)
for r in rows: by[r["date"]][r["category"]]=r["downloads"]
for day in sorted(by)[-3:]:
    d=by[day]; print("  {} by OS: macOS {} · Windows {} · Linux {} · no-UA {}  (macOS/Windows = real people)".format(day, d.get("Darwin",0), d.get("Windows",0), d.get("Linux",0), d.get("null",0)))' 2>/dev/null

echo "-- Hacker News"
curl -s "https://hacker-news.firebaseio.com/v0/item/$HN_ITEM.json" | python3 -c '
import sys,json,time; d=json.load(sys.stdin) or {}
print("post {}: {} points · {} comments · {:.0f}h old · title: {}".format(d.get("id"), d.get("score"), d.get("descendants"), (time.time()-d.get("time",time.time()))/3600, d.get("title")))' 2>/dev/null
curl -s "https://hn.algolia.com/api/v1/search?query=kvleak&tags=story" | python3 -c 'import sys,json; h=[x for x in json.load(sys.stdin)["hits"] if "kvleak" in (x.get("title") or "").lower()]; print("HN stories mentioning kvleak:", len(h))' 2>/dev/null

echo "-- Lists and directories"
gh pr view "https://github.com/$PR" --json state,comments,reviews --jq '"awesome-claude-skills PR: \(.state) · \(.comments|length) comments · \(.reviews|length) reviews"' 2>/dev/null
curl -s -A "$UA" "https://skills.sh/api/search?q=kvleak" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("skills.sh index: {} hit(s)".format(d.get("count", len(d.get("skills",[])))))' 2>/dev/null

echo "-- Reddit (API blocks anonymous clients; open in a browser)"
echo "https://www.reddit.com/search/?q=kvleak&sort=new"
