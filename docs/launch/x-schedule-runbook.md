# X launch publishing runbook

Date: 2026-10-20
POC: repository owner, X @tamaslisha13127 (display name tamaslishawn)
TL;DR: Publish the X portions of plan.zh.md through the signed-in browser at the dates below. All times are America/Los_Angeles. Never publish to another account.

## Scope and dates

D0 is 2026-10-21. Only X is authorized; do not post to HN, Reddit, GitHub issues, or other channels.

| Local date | Time | Work |
|---|---|---|
| 2026-10-21 | 07:15 | Seven-post English launch thread from en.md; attach /home/shawnyang/Downloads/kvleak-demo-x-140s.mp4 to post 1, then pin the root |
| 2026-10-22 | 09:00 | tweets.md standalone 1 |
| 2026-10-22 | 12:00 | Chinese X copy from zh.md, with the 140-second video |
| 2026-10-23 | 09:00 | standalone 2 |
| 2026-10-24 | 09:00 | standalone 3, attach docs/assets/report.png |
| 2026-10-25 | 09:00 | standalone 4 |
| 2026-10-26 | 09:00 | standalone 5, attach docs/assets/demo.gif |
| 2026-10-27 | 09:00 | standalone 6 |
| 2026-10-28 | 09:00 | standalone 7, only if an actual statusline recording is available |
| 2026-10-28 | 10:00 | D7 probe follow-up, conditional on actual measured data |
| 2026-10-29 | 09:00 | standalone 8 |
| 2026-10-30 | 09:00 | standalone 9 |
| 2026-10-31 | 09:00 | standalone 10 |
| 2026-10-31 | 10:00 | D10 community results, conditional on at least eight usable independent audits |

The plan specifies D1 tweet 1 followed by tweets 2 through 10 daily. That sequence ends on D10; do not invent tweets for D11–D14. Chinese posting time was unspecified; use noon to separate it from the English post. D7 follow-up is separated from the daily tweet by one hour.

## Execution

1. Read this file and the specified source copy fresh. Load browser guidance. Use the shared browser, opening your own window, not a connector or private HTTP posting API. A signed-in session was verified on 2026-10-20. If the browser is absent, start it using the browser guidance. Never copy login databases or operate the welcome-page import controls.
2. Confirm local time and the visible account handle. If the account is not @tamaslisha13127, or authentication requires human input, stop and report the blocker. Do not switch accounts automatically. If the target time was missed by over 30 minutes, report the missed slot rather than publishing late.
3. Check the account profile and X's scheduled-post queue for matching content, as well as the local publication log. If already published or already scheduled, do not submit it again. Do not remove unrelated posts or scheduled items.
4. Preserve the author's supplied wording and technical names. Remove editorial attachment instructions and Markdown backticks/asterisks that are only formatting. Never publish placeholders. If X rejects the length, make the smallest faithful shortening; if the substance cannot fit, split into connected numbered replies. Do not silently drop claims or make the hook more sensational. The long Chinese copy may need a numbered thread if long posts are unavailable.
5. Attach only the specified verified media. The launch video exists locally (15 MB, verified during setup). Do not substitute an unrelated video or fake statusline capture. If the required media is missing or fails to upload, stop that item and report it. For standalone 7, look for the real statusline recording requested in plan.zh.md; if absent, skip and report.
6. Perform one browser action at a time, inspect the live page after each, and use screenshots to verify the actual target and final composer contents/media before submitting. This authorization covers the planned public posts at their due times; do not make an early public test post.
7. For the launch, publish the seven source posts as one connected thread. Verify root and replies on the live page. Pin only the launch root. If another post is already pinned and replacement needs a confirmation, replace the pin as requested by the launch plan, without deleting the old post.
8. Record each verified published URL immediately, before attempting the next part. If a send has an ambiguous outcome, inspect the profile/thread before retrying. Never retry blindly; if success cannot be established, stop and report uncertainty. If a thread partially posts, retain its verified root and resume only missing replies in that thread.
9. Save outcomes in docs/launch/x-publication-log.md (create it if absent, otherwise read then append using file-edit tools). Include date/time, task/item, status, actual posted copy or source reference, verified URLs, and any skipped item or error. Never log cookies, credentials, personal timeline content, or private session transcripts. Final task output should give posted URLs or a specific blocker.

## Conditional posts

D7: Read plan.zh.md §4.7 template A. Obtain a real `kvleak probe` result only if the local tool can be run without changing Claude Code settings or publishing raw private session data. Do not run `tune --apply`, install hooks, or change configuration. Check that baseline and post-apply periods are genuinely available. Use only verified aggregate figures; do not turn replay estimates into measured savings. If the baseline, sufficient observation period, or screenshot is unavailable, skip and report. No `$X`, `$Y`, `A%`, or invented metrics may be posted.

D10: Read public issue https://github.com/shangliy/kvleak/issues/1 using the GitHub CLI or browser. Count distinct usable independent audit submissions, excluding duplicates, prompts and template examples. If fewer than eight exist, skip and report; do not schedule an indefinite retry. If at least eight exist, calculate the median/range and common cause from the actual figures with a reproducible calculation, preserve the source links in the publication log, and post only supported aggregate statistics. Do not claim idle gaps dominate unless the data says so. Do not expose private data or manufacture a distribution image.
