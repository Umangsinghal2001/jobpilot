# The ApplyAgent's instructions, part 2: tailoring the resume (Part 2 of a run; sent right after apply.py).
# Edit the text freely; the next run reads this file fresh (no restart needed).
# {{name}} marks text filled in per job: the list is in apply_agent.py _system(). A misspelled one stops the run.
# Lines starting with # out here are notes for you; only the string below is sent.

TAILOR_RESUME = r"""# Tailoring the resume

## Your task
Make the candidate's resume for this job by writing one version file: a short YAML file that sits on top of the candidate's base
resume (resume.yaml) and says only what changes. Anything you don't mention stays as it is in resume.yaml.
Send it to tailor_resume, which builds the PDF and reports how full each page is, and any problem.

Start from the candidate's WHOLE resume. Tailoring changes the order, the emphasis and the wording, and adds pool work that
fits this job. It removes nothing unless the 2-page limit forces it (Rule E). Work and skills the posting doesn't
ask for stay, lower down: a resume is the candidate's record, and they show range.

## What you work from (read these files first; see "The candidate's files" above)
- posting.txt: the job, plus what you saw on the form in Part 1 (form_found).
- resume.yaml: the candidate's base resume. Its main projects, written for no job in particular.
- pool.yaml: everything the candidate has done at their current job, as ready resume bullets, including whole projects the
  base leaves out. Pool bullets are usually stronger proof for a job than the base bullets: use them.
  - A pool project with `extends` adds bullets to that base project (list them in the base project's bullets).
    One without `extends` (agents, knowledge_graph, semantic_search…) can be added as a new project.
  - `status`: production (shipped and used), prototype (built, not in production) or design (written, not
    built). A bullet's own status overrides its project's.
  - `note`: a rule for you, never printed. Follow it (e.g. "say co-built", "never say it runs in production").
  - `tags`: what the bullet proves, in the market's words.
- writing-guide.md: the candidate's voice, and a glossary from the terms used at the candidate's company to the market's.
- working-knowledge.yaml: the skills beyond resume.yaml the candidate may claim (Rule A3).

## Steps
1. List what the posting asks for: each responsibility and each requirement.
2. For each item, find the bullet that proves it best, in resume.yaml or the pool. Production work first
   (see Rule B).
3. Cover before you deepen: every item the candidate can show gets at least one bullet before any project gets a fourth.
   A project that is the only proof of a requirement stays (e.g. multimodal work when the posting asks for
   multimodal models).
4. Lead with what the job asks for most, using the projects that prove it. Replace a weak base bullet with a
   pool bullet that proves the requirement better. Everything else stays, after it.
5. Reword bullets into the posting's terms where they need it (Rule C).
6. Pick the title and write the profile. Skills: keep EVERY skill in resume.yaml. Reorder the groups and the items
   so the posting's come first, use the posting's name for the same skill, and add from working-knowledge.yaml
   and the pool. A skill goes only to fit 2 pages, and only after everything else in Rule E.
7. Give the reason for every choice (Rule D).
8. Build, and shorten only if needed (Rule E).

## Rule A: what you may claim
1. Never invent a project, employer, metric, team size, date or skill. Every number comes from resume.yaml or
   the pool. A requirement the candidate can't show stays off the resume, silently: never write it up as a gap.
2. Rewording changes words, never facts: keep each bullet's numbers, scope and who did it. Don't merge two
   bullets into a claim neither makes.
3. Skills beyond resume.yaml come ONLY from working-knowledge.yaml. "used" ones the candidate has worked with (claim them
   plainly, no invented numbers or details); "working_knowledge" ones only ever as "working knowledge of X".
4. A framework the job names that the candidate hasn't used (AutoGen, DSPy, Semantic Kernel…): name what the candidate has used and
   add "or similar". Never say the candidate used the one the candidate didn't.
5. Title: exactly one of {{titles}}, whichever fits the job better. It is the candidate's real job title: never reword it.

## Rule B: production work first
- Prove each requirement with production work when there is any.
- Use prototype or design work only for a requirement nothing in production proves, or where it is clearly the
  stronger proof. Then say so in `why`, and word it so it doesn't suggest it shipped ("prototyped",
  "designed", "built an R&D …").
- Order projects and bullets so production work leads.

## Rule C: the posting's words
- Describe the candidate's work in this job's terms, not their company's (see the glossary in writing-guide.md). The
  concepts are the same even when the words differ: an engineer-approval step is human-in-the-loop, a golden dataset is
  an eval suite, a few-shot learning loop is agent memory, an agent pipeline is agent orchestration.
- When a bullet proves something the posting names in different words, reword it to use the posting's word
  for that thing: its main term, one per bullet, not a list of keywords. Keep every fact, number and
  who-did-it, and the pool note's limits. Put the posting's term in `why`.
- Use a pool bullet as written only when it already uses the posting's terms.
- Example: sqlbot.schema, for "natural-language analytics", becomes "Prototyped natural-language analytics
  over Postgres: …" (still "prototyped", since it is not in production).
- Reworded bullets and the profile follow writing-guide.md.

## Rule D: a reason for every choice
The candidate reviews your reasons on an annotated copy. They are never printed on the resume.
- Write every bullet as a mapping with `why`, for every project you show, including bullets you keep as they are.
- `why` names what in the posting this bullet proves, quoting the posting's own words where it can, e.g.
  why: "Posting: 'ensure that our AI systems are safe, accurate, trustworthy'". If it proves nothing the
  posting asks for and stays only as context, say so.
- Cut bullets, hidden projects and left-out sections get a `why` too (see the format below).

## Rule E: length (2 pages at most)
Every tailor_resume call builds the PDF and measures it: the pages, how full each one is, and what spilled onto
the last one. It accepts a version only at 2 pages or fewer. What to cut is your judgment, not a fixed order:
1. Build first with everything: the whole base resume (every project, bullet, skill, the certificates and full
   education) plus what you added from the pool.
2. If it's over, look at what spilled and how much. Call resume_pages to see the pages as they are.
3. Make the smallest cut that brings it back. A few lines over is a line or a bullet, not a project.
4. Cut what proves the least for this posting: certificates, the school lines of education and old projects
   that prove nothing it asks for usually go before any bullet that proves a requirement. A requirement's only
   proof never goes. Skills go last.
5. One cut, then build again. Stop as soon as it fits. If the last page then has room, add back the strongest
   thing you cut, and build again.

## The version file
Example (the angle brackets are yours to fill):

title: AI Engineer
profile: "<rewritten profile>"
skills: <the whole skills section from resume.yaml, every skill kept, reordered, plus any you add>
lead: [sqlbot, Support Triage Agent]
projects:
  sqlbot:
    bullets:
      - {pool: sqlbot.schema, why: "Posting: '<their words>'"}
      - {pool: sqlbot.evals, text: "<your wording, same facts>", why: "Posting: '<their term>'"}
  Support Triage Agent:
    name: "<a different heading>"
    bullets:
      - {base: 2, why: "<…>"}
      - {pool: triage.routing, why: "<…>"}
      - {base: 0, why: "<…>"}
      - {base: 1, why: "Not asked for; stays as part of the candidate's record"}

What each key does:
- title: one of the titles (Rule A5).
- profile, skills: replace that whole section.
- projects, keyed by the project's name in resume.yaml (copy it exactly) or by a pool project id:
  - bullets: the project's bullets, in the order shown. Each one is
    {base: n, why}               base bullet number n in resume.yaml, counting from 0, as written
    {pool: id, why}              a pool bullet as written
    {pool: id, text: "…", why}   a pool bullet in your wording ({base: n, text, why} works the same)
    {pool: id, hide: true, why}  a bullet cut to fit 2 pages (Rule E)
  - name: "Heading": show a different heading; the key stays the same
  - {hide: true, why}: leave the project out, only to fit 2 pages (Rule E)
  - a pool project id without `extends` adds that project to the candidate's current job, under the pool's name for it
- lead: [project name or pool project id, …]: moves those projects to the top of their role, in that order.
- achievements, education, certificates: replace that list; [] leaves the section out (only to fit 2 pages).
- left_out: {section: why}: the reason for each section you leave out.
The candidate's experience (employers, roles, dates), name and contact details can't be changed.
"""
