# Writings

A personal site for poems, blogs and quotes in Gujarati, Hindi and English.

It is one folder of static files. No server, no database, no build step, no
account with anyone. GitHub hosts it for free and always has; your writing lives
as a JSON file inside this repository, which means it is versioned, backed up,
and yours. **There is nothing here that can start charging you.**

---

## Why this shape

| Need | How it is met | What it costs |
| --- | --- | --- |
| Hosting | GitHub Pages | Free, permanently, for public repos |
| Storage | `data/content.json` in this repo | Free — it is just a file |
| Editing | Admin panel in the browser | Free |
| Publishing | A commit through the GitHub API | Free |
| Fonts | Google Fonts, loaded on demand | Free |
| Feedback | Your email, or any free form service | Free |
| Backups | Git history + an export button | Free |

The one thing a static site genuinely cannot do is *receive* a message. That is
the only place an outside service appears, and it is optional — see
[Feedback](#feedback).

---

## Turning it on

### 1. Publish the site

In this repository on GitHub: **Settings → Pages**. Under *Build and
deployment*, set **Source: Deploy from a branch**, pick the `main` branch and
the `/ (root)` folder, and save.

A minute later the site is live at:

```
https://penpaper0878.github.io/My-Writings/writings/
```

Open it on your phone and use *Add to Home Screen* — it installs like an app and
works offline.

### 2. Become the admin

Scroll to the bottom of any page and tap the small **lock**. The first time, you
choose a passphrase; after that it unlocks the editing tools on that device.

You will see a **+** button appear in the corner and a **gear** in the header.

### 3. Make it yours

Gear → **Site**: your name, the site's name, a photo, links, a contact email.
The site name can be different in each language if you want it to be.

Gear → **Theme**: eight palettes, light/dark/automatic, a custom accent colour,
page backgrounds, a separate font for each of the three scripts, and — under
*Fonts for headings* — a separate font and weight for titles, so headings and
body text can differ.

Gear → **Home page**: the layout, how many pieces show before *See more*, which
tabs exist, and what the big header says.

### 4. Set up publishing

Gear → **Publish**.

The owner and repository should already be filled in. You need one token:

1. Go to **github.com → Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token**.
   (Or use the button in the Publish panel, which links straight there.)
2. **Repository access:** *Only select repositories* → `My-Writings`.
3. **Permissions → Repository permissions → Contents:** *Read and write*.
   Nothing else. That is the whole list.
4. Generate it, copy it, and paste it into the Publish panel.
5. Press **Test connection**. It should say it is connected.

Now **Publish** pushes your work live in about a minute.

> **You paste the token once per device, not once per poem.** It stays in that
> browser — locking the site does not remove it. To read it back later, open
> Settings → Publish and tap the eye beside the Token field; GitHub never shows
> a token a second time, so that field is the only place it still exists. There
> is a *Remove token from this device* button when you actually want it gone.
>
> It is never written into any file this site serves, so no visitor can read it.
> If you lose the device, revoke the token on GitHub and make a new one.

---

## Writing

Press **+**, or just hit `N`. The editor has six tabs; only the first is needed.

- **Write** — the kind (poem, blog or quote), the title and the words. The
  language is picked for you from the script you type in; change it if it guesses
  wrong. Your line breaks are kept exactly as typed — in a poem the breaks *are*
  the poem. Select words and press **B**, **I** or **U** to make them bold,
  italic or underlined; press again to undo it. Tags, collections, the date, a
  credit line, *Pin to the top* and *Keep as draft* are all on this tab too.
- **Look** — eight ready-made designs, shown with your own words. One tap sets
  the font, colours, background and frame together; adjust any of it afterwards.
- **Text** — typeface, size, line spacing, weight, italic, underline,
  alignment and colour for the body of the piece.
- **Heading** — the same controls for the title on its own: a different
  typeface, size, weight, italic, underline, colour or alignment from the body.
- **Background** — the theme's, one colour, a gradient, or a photograph.
  Gradients take as many colours as you like (*Add a colour*), each with its own
  position, in a straight line at any angle or as a circle.
- **Frame** — nine frames (side line, box, double, corners, card, ornament,
  notebook, quote mark, stamp) or none, each with its own colour and, where it
  applies, thickness, line style, corner rounding and space
  inside.

The preview at the top updates as you go, and images and PDFs you share use the
same design.

**Save** keeps the piece on this device; **Save & publish** puts it live in one
step. If the editor is closed by accident — a tap outside, a lost tab — the next
time you open it offers to bring back what you were writing.

Keyboard: `N` for a new piece; `Ctrl/Cmd + S` saves inside the editor and
publishes everywhere else.

### Finding and changing what you have written

Gear → **Content** lists everything, newest first — drafts included, which the
home page deliberately hides from readers. Search it, narrow it to poems, blogs,
quotes or drafts, and tap any row to edit it.

Each row shows which collections the piece is in and carries three controls:
pin it to the top of every list, switch it between draft and visible, or delete
it. **Select** turns on
checkboxes so you can act on several at once — add them all to a collection
(new or existing) in one go, or delete them, which is useful for clearing out
the sample pieces this site ships with.

### Tags

Tags are how you get through a few hundred pieces. In the editor, type a tag
and press Enter (or a comma), in any script — `ગઝલ` works as well as `love`.
Tags you have used before are offered underneath, one tap to add.

Every tag on the site is then clickable: on a card, inside a piece, or from the
tag button next to the language filter, which lists all of them with counts.
Clicking the tag that is already filtering turns it off again.

### Collections

A collection gathers pieces that belong together — a series, a book, a season.
Readers see each one as a cover on the *Collections* tab.

- **While writing:** the *Collections* row on the Write tab — tap a collection
  to put the piece in it, or **New collection** to make one on the spot.
- **From Gear → Collections:** create one by name, then **Add or remove pieces**
  opens a searchable list of everything you have written; tap pieces to tick
  them in or out. Each collection also takes a description, a cover photo or
  colour, and arrows to change the order readers see them in. **View** opens it
  as a reader would.
- **From Gear → Content:** **Select** several pieces, then **Add to
  collection**.

### Bringing in writing you already have

Gear → **Data → Import .txt files**. Select as many as you like; each becomes
one piece, the filename becomes the title, and the language is detected from the
script. Everything arrives as a draft so you can review before publishing.

---

## Sharing

Open any piece and press **Share**:

- **Save as image** — a picture sized for a post, a story or a wide card, drawn
  with your own fonts and colours and signed with your name.
- **Save as PDF** — opens the print dialog; choose *Save as PDF*.
- **Copy as text**, **Download .txt**, **Copy link**, **WhatsApp**, and the
  system share sheet on phones.

> **On link previews.** When you paste a link to one piece into WhatsApp or
> Facebook, the preview card will show the site's name, not the poem. That is
> not a fault you can fix here: the whole site is one file, and the apps that
> build those previews do not run the JavaScript that would pick out the piece.
> Fixing it properly would mean generating a separate HTML file per piece,
> which means a build step — and a build step is the thing that eventually
> breaks or starts costing money. **Share the image instead:** it carries the
> actual words, looks better in a chat, and needs nothing from anyone.

> **Why PDF goes through the print dialog.** JavaScript PDF libraries need an
> embedded font carrying full Indic shaping tables, and mostly render Gujarati
> and Devanagari wrong — broken matras, conjuncts falling apart. The browser's
> own print engine is the same one drawing the text on screen, so it is always
> correct. It is also free and needs no library.

---

## Feedback

Readers get a feedback form with an optional name and email, and a dropdown so
they can point at one specific piece.

The link only appears once a message has somewhere to go. Two ways:

**Email (nothing to set up).** Gear → Site → Contact email. Send opens the
reader's own mail app with the whole message already written. Always works,
never breaks, costs nothing.

**A form service (messages arrive on their own).** Gear → Feedback → Form
endpoint. Any service that accepts a JSON POST works:

| Service | Endpoint | Notes |
| --- | --- | --- |
| Web3Forms | `https://api.web3forms.com/submit` | Also paste the access key |
| Formspree | `https://formspree.io/f/xxxxxxxx` | No key needed |
| Getform | `https://getform.io/f/xxxxxxxx` | No key needed |
| Google Apps Script | your deployed web app URL | Free, and entirely yours |

If the service ever disappears or starts charging, change one field. The email
fallback is always there underneath.

The form carries a hidden honeypot field that silently absorbs bot submissions.

---

## What "only I can upload" actually means

Worth being precise, because this is a static site and every line of its code is
public.

**The passphrase is a convenience, not a wall.** It is stored as a slow PBKDF2
hash (250,000 rounds, salted), so it cannot be read back out of the file — but a
determined visitor could still flip a flag in their browser's devtools and see
the admin panels.

**That costs nothing, because it changes nothing.** Editing only ever writes to
*their own browser's* storage. The live site is a file in your repository, and
changing it requires the GitHub token — which exists only in your browser and in
no file this site serves. A visitor has nothing to find and nothing to push.

So: the passphrase keeps the interface out of the way, and the token is what
actually makes the site yours.

---

## Backups

Gear → **Data**:

- **Backup (.json)** — the whole site in one file: every piece, every collection,
  every setting. Keep one somewhere safe.
- **All writing (.txt)** — just the words, readable anywhere, forever.

Every publish is also a Git commit, so the repository already holds every version
you have ever published. Nothing is ever really lost.

**On importing.** A backup file carries settings as well as writing — colours,
fonts, background images, links. Values arriving that way are validated before
they reach the page: markup in a title or a poem is shown as text, a colour that
is not a colour falls back to the theme's, and a background image must be a
plain https or inline image. Still, only restore backups you made yourself.

---

## A custom domain

Buy a domain anywhere, then in **Settings → Pages → Custom domain** enter it and
add the DNS records GitHub shows you. HTTPS is issued free and renews itself.
Hosting stays free.

---

## The files

```
writings/
├── index.html              the page shell
├── manifest.json           installable-app metadata
├── sw.js                   offline cache
├── data/content.json       ← your entire site lives here
├── icons/                  app icons
└── assets/
    ├── css/app.css         all styling; every value is a CSS variable
    └── js/
        ├── store.js        the data model, loading, saving, the admin gate
        ├── i18n.js         interface strings in gu / hi / en
        ├── theme.js        palettes, the font catalogue, backgrounds
        ├── ui.js           icons, sheets, toasts, form controls
        ├── rich.js         bold / italic / underline inside a piece
        ├── views.js        header, hero, tabs, cards, the reading view
        ├── share.js        image, PDF, text, link, import and export
        ├── publish.js      committing to GitHub
        ├── editor.js       the writing editor and the admin unlock
        ├── settings.js     the settings panels
        ├── feedback.js     the feedback form
        └── app.js          routing and start-up
```

Plain scripts, no bundler, no dependencies. Open `index.html` through any local
web server and it runs.

---

## If something goes wrong

**Publish says the token was rejected.** It expired or was revoked. Make a new
one and paste it in.

**Publish says there is no permission.** The token needs *Contents: Read and
write*, and this repository must be in its *Only select repositories* list.

**After renaming the repository.** GitHub Pages moves the site to the new
address (`https://<you>.github.io/<new-name>/writings/`) and the old one stops
working. Your token keeps working, and when the site is opened at the new
address it publishes to the renamed repository by itself.

**The site did not update after publishing.** GitHub Pages takes up to a minute.
Check the **Actions** tab for the deployment, then hard-refresh.

**Fonts look wrong.** Google Fonts was unreachable; the site falls back to your
device's own Gujarati and Devanagari fonts and stays readable. It fixes itself on
the next load.

**The site says your writing could not be loaded.** The connection dropped or
GitHub was slow. Nothing is lost and nothing is overwritten: editing and
publishing stay switched off until the real content loads, so an empty page can
never be published over your work. Press *Try again*, or reload.

**I published, refreshed, and my new piece was not there.** GitHub Pages takes
a minute or two to serve a new version. Your device keeps showing what you just
published until the live site catches up, and publishing always reads the live
copy first and merges with it, so pieces added from another device are kept.

**Everything vanished on a device.** That device had an unpublished draft and
lost its storage. Your published site is unaffected — reload it. Restore a
backup from Gear → Data if you had local work. Every publish is a commit, so
older versions are also in the repository's history.

**It says "unpublished changes" after I wrote something.** That is the normal
state, not a problem. Writing saves to your device immediately; the live site
only changes when you press **Publish**. The badge clears once you do.

**Locked out of admin.** Edit `data/content.json` in GitHub directly, empty the
`admin.passHash` and `admin.passSalt` strings, and commit. The next unlock lets
you set a new passphrase.
