# OpusDeshboard - Lisa chatbot + Telegram-style dashboard

Ek hi repo, do tarah se chalta hai (deploy karte waqt **sirf ek** value bharo):

| Mode | Kya bharna hai | Kya milta hai |
|---|---|---|
| **Bot** | `BOT_TOKEN` (@BotFather se) | Normal Telegram bot jo Lisa ki tarah reply karta hai. Dashboard nahi hota. |
| **Account (userbot)** | `STRING_SESSION` (`python gen_session.py`) | Tumhara apna account Lisa ki tarah reply karta hai **+ `/admin` par poora Telegram-style dashboard**. |

- Dono bhare ho toh bot start hi nahi hota aur clear error deta hai ("sirf ek hi fill karo"). Dono khali ho toh bhi error.
- `API_ID`, `API_HASH`, `MONGO_URI`, `DEEPSEEK_API_KEY` dono modes me chahiye (Pyrogram bot token ke saath bhi API_ID/HASH maangta hai).
- Bot mode me: sticker packs aur dashboard nahi hote. Owner commands ke liye `OWNER_IDS` bharo (neeche dekho).

## Owner commands + multi-owner
`OWNER_IDS=111111111,222222222` (comma se alag Telegram user ids). Account mode me account khud hamesha owner hai, `OWNER_IDS` se extra log add hote hain. Bot mode me sirf `OWNER_IDS` wale owner hain (khali = commands band). Purana `OWNER_ID` bhi chalta hai.

| Command | Kaam |
|---|---|
| `.chatoff` / `.chaton` | is chat me AI band / chalu |
| `.stickers` | account ke sticker packs reload (sirf account mode) |
| `.tag <msg>` | **group ke saare members ko tag karta hai**: har line `Full Name msg` (naam clickable mention) |
| `.tag admins <msg>` | sirf admins ko tag |
| `.stop` | us group ki tagging turant rok deta hai |

`.tag` ke baare me: sirf owners aur sirf groups me; bots aur deleted accounts skip; ek message me `TAG_BATCH` (5) mentions, beech me `TAG_DELAY` (3s) gap, FloodWait aaye to apne aap ruk ke dobara; ek group me ek hi run (doosra `.tag` "chal rahi hai" bolta hai); ek run me max `TAG_MAX` (1000). Bot mode me group me `/tag hello` likho (bot privacy mode me bina `/` wale messages nahi dikhte) aur bot ko group me **admin** banao, warna members ki list nahi milti. Account mode me bhi badi groups me admin hona padta hai. Note: sabko tag karne se sabko notification jaati hai - sirf apni groups me, kam use karo.

## Lisa dataset
`data/lisa_examples.jsonl.gz` (16,690 example chats, synthetic) + `lisa.py`. Har incoming message ke liye sabse milte-julte 3 examples (alag sub-topics se) system prompt me "TONE EXAMPLES" ban kar jaate hain, saath me Lisa ke style rules (pehle reaction, phir comment, max ek sawal, pichli baat yaad rakhna, koi romantic partner role nahi). Fine-tuning nahi, sirf few-shot.
`LISA_DATASET=0` se band, `LISA_EXAMPLES=0-8` se count. Default `PERSONA_NAME=Lisa`.

## Dashboard (sirf STRING_SESSION + ADMIN_PASSWORD)
`https://<tumhari-app>/admin` -> password -> Telegram Web jaisa app:

**Chats**: tabs **All / Personal / Bots / Groups / Channels / Blocked / Deleted** (counts ke saath), search (+ global search), unread/pin, live updates.
Chat me: history (purane messages scroll karke), send, **reply**, **edit**, **delete (for me / everyone)**, forward, file/photo upload, photo/sticker/video/voice/file/poll/location/contact dikhna, download, chat me search.
Chat menu: info, mark read/unread, pin, **mute/unmute**, archive, **block/unblock**, clear history, delete chat, leave group/channel, **AI on/off (is chat ke liye)**.

**Settings** (Telegram jaisa):
- **Edit profile**: first/last name, **bio**, **username** (live availability check), **phone number** (naye number pe code -> confirm), profile photo set/remove.
- **Notifications**: private / groups / channels ke liye on-off + preview.
- **Privacy & security**: last seen, phone number, profile photo, bio, forwards, calls, invites, voice messages, number se dhundhna (Everybody / My Contacts / Nobody); **blocked users** (unblock / username se block); **devices (active sessions)** (ek ya baaki sab end); account self-destruct timer.
- **Chat settings**: theme (light/dark/auto), text size, Enter se send, "open karne par read mark" (default off), default auto-delete timer.
- **Contacts**: list/search, add (phone ya @username), delete, seedha message.
- **AI bot**: master on/off switch, mode, persona, Lisa dataset status, provider status.
- **AI chat log**: bot ne kis se kya baat ki (masked, `LOG_DAYS` baad auto-delete).

Jo **nahi** hai (Telegram API se mumkin hai par abhi nahi banaya): calls, secret chats, stories, 2-step verification password, chat folders edit, sticker/emoji pack management, voice recording, Premium features.

### Dashboard ki security (zaroor padho)
Ye dashboard tumhare **poore account** ko control karta hai. Isliye:
- `ADMIN_PASSWORD` lamba aur unique rakho (min 8 chars, rate-limit + 7 din ki signed HttpOnly cookie, `SameSite=Strict`, HTTPS par `Secure`).
- Har write (send/edit/delete/settings) par **CSRF token** + Origin check. Page me koi inline script nahi (strict CSP), chat text sirf `textContent` se dikhta hai (XSS nahi).
- File/media: sirf plain image/video/audio inline dikhte hain; baaki sab (html, svg, pdf, exe) download hote hain `application/octet-stream` se.
- Khatarnak actions (delete chat, leave, clear, sessions end, number badalna) ke liye confirm zaroori; `DASH_ALLOW_DANGEROUS=0` se delete/leave/clear/sessions band ho jaate hain.
- Jis session se bot chal raha hai usse dashboard end nahi karne deta.
- Dashboard bot mode me **register hi nahi hota** (routes hi nahi).
- Dashboard ke "read" button/setting se hi dusre ko blue tick dikhta hai; chat kholne par apne aap read **nahi** hota (setting me on kar sakte ho).

## Deploy (Render)
1. Repo Render pe connect karo (Docker). `render.yml` me env vars hain.
2. **Ek hi** mode bharo: `BOT_TOKEN` **ya** `STRING_SESSION`. Dusra khali (ya variable hi mat banao).
3. `MONGO_URI`, `DEEPSEEK_API_KEY` (+ fallbacks), `API_ID`, `API_HASH`. Account mode me `ADMIN_PASSWORD` bhi.
4. Deploy -> `/health` OK -> account mode me `/admin`.

String session: `python gen_session.py` (local, apne account se login). Session string kisi ko mat dena.

Important: jab tumhare account se Lisa (AI) reply karti hai toh samne wale ko lagta hai tum baat kar rahe ho. Prompt me rule hai ki koi seriously pooche toh sach bata do ki AI hai; zaroorat ho toh `.chatoff` / dashboard ke AI switch se band rakho.

## Tests
```
python -m unittest discover -s tests -t . -v      # 250+ tests, Telegram/MongoDB/network ke bina
```
Front-end tests (`tests/ui/*.js`) Node.js hone par apne aap chalte hain.
**Dhyan do:** tests fake Telegram client par hain. Asli Telegram par pehli baar chalate waqt logs dekho: agar koi dashboard action "Action fail hua (...)" bataye toh Pyrogram/PyroTGFork version ka fark ho sakta hai - us method ka naam `tgadmin.py` me ek jagah hai.
