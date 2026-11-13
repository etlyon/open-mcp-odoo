"""The sign-in pages a person sees: the address, the key (or, for an app with
no address step, the token), and (only for an assistant we do not know) one "Allow?".

Plain HTML, no script. The look is the operator's: with a brand folder
(settings ``[brand]``) the pages use its stylesheets and logo in a header band;
without one, a small neutral style. Every page names the assistant that is
asking and links to the source code at its foot (AGPL-3.0 section 13).
"""

from __future__ import annotations

import html
from dataclasses import dataclass

LANGS = ("en", "fr", "de")

T: dict[str, dict[str, str]] = {
    "title.connect": {"en": "Connect {app} to {assistant}", "fr": "Connecter {app} à {assistant}", "de": "{app} mit {assistant} verbinden"},
    "lead": {"en": "{assistant} will use {app} as you, with your own rights — nothing more.",
             "fr": "{assistant} utilisera {app} en votre nom, avec vos propres droits — rien de plus.",
             "de": "{assistant} nutzt {app} als Sie, mit Ihren eigenen Rechten — nicht mehr."},
    "wants.unverified": {
        "en": "“{name}” ({host}) wants to use your {app}. It is not an assistant we know: continue only if you added it yourself.",
        "fr": "« {name} » ({host}) veut utiliser votre {app}. Ce n’est pas un assistant que nous connaissons : continuez seulement si vous l’avez ajouté vous-même.",
        "de": "„{name}“ ({host}) möchte Ihr {app} verwenden. Diesen Assistenten kennen wir nicht: Fahren Sie nur fort, wenn Sie ihn selbst hinzugefügt haben."},
    "address.label": {"en": "{app} web address", "fr": "Adresse web de votre {app}", "de": "Webadresse von {app}"},
    "connecting": {"en": "Connecting to", "fr": "Connexion à", "de": "Verbindung mit"},
    "change": {"en": "Change", "fr": "Modifier", "de": "Ändern"},
    "next": {"en": "Next:", "fr": "Ensuite :", "de": "Als Nächstes:"},
    "step.open": {"en": "Open {app}", "fr": "Ouvrir {app}", "de": "{app} öffnen"},
    "step.add.odoo": {"en": "Your picture › My Preferences › Security › Add API Key",
                      "fr": "Votre photo › Mes préférences › Sécurité › Ajouter une clé API",
                      "de": "Ihr Bild › Meine Einstellungen › Sicherheit › API-Schlüssel hinzufügen"},
    "step.paste": {"en": "Paste it below", "fr": "Collez-la ci-dessous", "de": "Unten einfügen"},
    "key.label": {"en": "API key", "fr": "Clé API", "de": "API-Schlüssel"},
    "key.placeholder": {"en": "Paste your key here", "fr": "Collez votre clé ici", "de": "Schlüssel hier einfügen"},
    "db.label": {"en": "Database name", "fr": "Nom de la base de données", "de": "Name der Datenbank"},
    "continue": {"en": "Continue", "fr": "Continuer", "de": "Weiter"},
    "connect": {"en": "Connect", "fr": "Connecter", "de": "Verbinden"},
    "not.now": {"en": "Not now", "fr": "Pas maintenant", "de": "Nicht jetzt"},
    "try.again": {"en": "Try again", "fr": "Réessayer", "de": "Erneut versuchen"},
    "back.to": {"en": "Back to {assistant}", "fr": "Retour à {assistant}", "de": "Zurück zu {assistant}"},
    "ok.title": {"en": "{app} is connected to {assistant}.", "fr": "{app} est connecté à {assistant}.", "de": "{app} ist mit {assistant} verbunden."},
    "ok.who": {"en": "Signed in as {who}", "fr": "Connecté en tant que {who}", "de": "Angemeldet als {who}"},
    "ok.back": {"en": "You can go back to {assistant} now.", "fr": "Vous pouvez revenir à {assistant}.", "de": "Sie können jetzt zu {assistant} zurückkehren."},
    "ok.wrong": {"en": "Not the right account? Delete the key in {app}, then connect again.",
                 "fr": "Pas le bon compte ? Supprimez la clé dans {app}, puis connectez-vous à nouveau.",
                 "de": "Falsches Konto? Löschen Sie den Schlüssel in {app} und verbinden Sie sich erneut."},
    "fail.title": {"en": "{app} didn't connect. Nothing was changed.", "fr": "{app} n’a pas été connecté. Rien n’a été modifié.",
                   "de": "{app} wurde nicht verbunden. Nichts wurde geändert."},
    "allow.title": {"en": "Allow “{name}”?", "fr": "Autoriser « {name} » ?", "de": "„{name}“ erlauben?"},
    "allow.line": {"en": "It will read and change what you can in {app}, as {who}.",
                   "fr": "Il pourra lire et modifier ce que vous pouvez dans {app}, en tant que {who}.",
                   "de": "Er kann in {app} lesen und ändern, was Sie können, als {who}."},
    "allow": {"en": "Allow", "fr": "Autoriser", "de": "Erlauben"},
    "key.online.odoo": {"en": "Odoo Online: the API works only on the Custom plan.",
                        "fr": "Odoo Online : l’API ne fonctionne qu’avec l’offre Custom.",
                        "de": "Odoo Online: Die API funktioniert nur im Custom-Tarif."},
    "connector": {"en": "{app} connector", "fr": "Connecteur {app}", "de": "{app}-Connector"},
    # Hub (Nextcloud): Grant access in Nextcloud (Login Flow v2), or paste an app password.
    # Nextcloud's real labels: its approval page shows "Log in" first, then "Grant access".
    "step.grant.nextcloud": {"en": "Click Log in, then Grant access",
                             "fr": "Cliquez sur Se connecter, puis sur Autoriser l’accès",
                             "de": "Klicken Sie auf Anmelden, dann auf Zugriff gewähren"},
    "step.back": {"en": "Come back here", "fr": "Revenez ici", "de": "Kommen Sie hierher zurück"},
    "paste.instead": {"en": "Paste an app password instead", "fr": "Coller plutôt un mot de passe d’application",
                      "de": "Stattdessen ein App-Passwort einfügen"},
    "step.add.nextcloud": {"en": "Your picture › Settings › Security › Create new app password",
                           "fr": "Votre photo › Paramètres › Sécurité › Créer un nouveau mot de passe d’application",
                           "de": "Ihr Bild › Einstellungen › Sicherheit › Neues App-Passwort erstellen"},
    # Nextcloud's "Create new app password" shows the user name and the password together: paste both.
    "step.paste.nextcloud": {"en": "Paste both below", "fr": "Collez-les ci-dessous", "de": "Beide unten einfügen"},
    "user.label": {"en": "User name", "fr": "Nom d’utilisateur", "de": "Benutzername"},
    "user.placeholder": {"en": "alice", "fr": "alice", "de": "alice"},
    "password.label": {"en": "App password", "fr": "Mot de passe d’application", "de": "App-Passwort"},
    "password.placeholder": {"en": "Paste your app password here", "fr": "Collez votre mot de passe d’application ici",
                             "de": "App-Passwort hier einfügen"},
    "ok.wrong.nextcloud": {"en": "Not the right account? Delete the app password in {app}, then connect again.",
                           "fr": "Pas le bon compte ? Supprimez le mot de passe d’application dans {app}, puis connectez-vous à nouveau.",
                           "de": "Falsches Konto? Löschen Sie das App-Passwort in {app} und verbinden Sie sich erneut."},
    # kSuite (Infomaniak): no address step; the person pastes an API token made in Infomaniak's Manager.
    # "Create a token" / "Créer un token" / "Token erstellen" and "API token" are Infomaniak's own words
    # (its help pages in each language); the scope names are Infomaniak's identifiers. "token" is masculine
    # in French (le token) and neuter in German (das Token).
    "step.open.ksuite": {"en": "Open Infomaniak", "fr": "Ouvrir Infomaniak", "de": "Infomaniak öffnen"},
    "step.add.ksuite": {"en": "Create a token with user_info, workspace:mail and workspace:calendar",
                        "fr": "Créer un token avec user_info, workspace:mail et workspace:calendar",
                        "de": "Token mit user_info, workspace:mail und workspace:calendar erstellen"},
    "step.paste.ksuite": {"en": "Paste it below", "fr": "Collez-le ci-dessous", "de": "Unten einfügen"},
    "token.label": {"en": "API token", "fr": "Token API", "de": "API-Token"},
    "token.placeholder": {"en": "Paste your token here", "fr": "Collez votre token ici", "de": "Token hier einfügen"},
    "ok.wrong.ksuite": {"en": "Not the right account? Delete the token in Infomaniak, then connect again.",
                        "fr": "Pas le bon compte ? Supprimez le token dans Infomaniak, puis connectez-vous à nouveau.",
                        "de": "Falsches Konto? Löschen Sie das Token bei Infomaniak und verbinden Sie sich erneut."},
}

LANG_NAMES = {"en": "English", "fr": "Français", "de": "Deutsch"}

ERRORS: dict[str, dict[str, str]] = {
    "empty": {"en": "Type your {app} address.", "fr": "Saisissez l’adresse de votre {app}.", "de": "Geben Sie die Adresse Ihres {app} ein."},
    "invalid": {"en": "This is not a web address.", "fr": "Ce n’est pas une adresse web.", "de": "Das ist keine Webadresse."},
    "not_https": {"en": "Use an address that starts with https://.", "fr": "Utilisez une adresse qui commence par https://.", "de": "Verwenden Sie eine Adresse, die mit https:// beginnt."},
    "userinfo": {"en": "Remove the name and password from the address.", "fr": "Retirez le nom et le mot de passe de l’adresse.", "de": "Entfernen Sie Name und Passwort aus der Adresse."},
    "query": {"en": "Remove everything after ? or # from the address.", "fr": "Retirez tout ce qui suit ? ou # dans l’adresse.", "de": "Entfernen Sie alles nach ? oder # aus der Adresse."},
    "port": {"en": "Use the address without a port number.", "fr": "Utilisez l’adresse sans numéro de port.", "de": "Verwenden Sie die Adresse ohne Portnummer."},
    "ip_literal": {"en": "Use the name of the server, not its number.", "fr": "Utilisez le nom du serveur, pas son numéro.", "de": "Verwenden Sie den Namen des Servers, nicht seine Nummer."},
    "not_public": {"en": "This address cannot be reached from the internet.", "fr": "Cette adresse n’est pas joignable depuis internet.", "de": "Diese Adresse ist aus dem Internet nicht erreichbar."},
    "not_found": {"en": "No server answers at this address.", "fr": "Aucun serveur ne répond à cette adresse.", "de": "Unter dieser Adresse antwortet kein Server."},
    "unreachable": {"en": "The server at this address did not answer.", "fr": "Le serveur à cette adresse n’a pas répondu.", "de": "Der Server unter dieser Adresse hat nicht geantwortet."},
    "timeout": {"en": "The server took too long to answer. Try again.", "fr": "Le serveur a mis trop de temps à répondre. Réessayez.", "de": "Der Server hat zu lange gebraucht. Versuchen Sie es erneut."},
    "too_large": {"en": "The server sent an unexpected answer.", "fr": "Le serveur a envoyé une réponse inattendue.", "de": "Der Server hat eine unerwartete Antwort gesendet."},
    "redirect": {"en": "This address sends visitors elsewhere. Use the address you log in to.", "fr": "Cette adresse renvoie ailleurs. Utilisez l’adresse où vous vous connectez.", "de": "Diese Adresse leitet weiter. Verwenden Sie die Adresse, unter der Sie sich anmelden."},
    "not_odoo": {"en": "This address is not an Odoo. Use the address you open Odoo with.", "fr": "Cette adresse n’est pas un Odoo. Utilisez l’adresse où vous ouvrez Odoo.", "de": "Diese Adresse ist kein Odoo. Verwenden Sie die Adresse, unter der Sie Odoo öffnen."},
    "too_old": {"en": "This Odoo is version {detail}. It needs Odoo 19 or newer.", "fr": "Cet Odoo est en version {detail}. Il faut Odoo 19 ou plus récent.", "de": "Dieses Odoo hat Version {detail}. Es braucht Odoo 19 oder neuer."},
    "no_api": {"en": "This Odoo does not offer its API at this address.", "fr": "Cet Odoo n’offre pas son API à cette adresse.", "de": "Dieses Odoo bietet seine API unter dieser Adresse nicht an."},
    "wrong_server": {"en": "This connection is for {detail}. Use that address, or add the app again.", "fr": "Cette connexion est pour {detail}. Utilisez cette adresse ou ajoutez l’app à nouveau.", "de": "Diese Verbindung gilt für {detail}. Verwenden Sie diese Adresse oder fügen Sie die App neu hinzu."},
    "bad_key": {"en": "Paste the whole key, without spaces.", "fr": "Collez la clé entière, sans espaces.", "de": "Fügen Sie den ganzen Schlüssel ohne Leerzeichen ein."},
    "key_refused": {"en": "Odoo refused this key. Make a new one and paste it again.", "fr": "Odoo a refusé cette clé. Créez-en une nouvelle et collez-la à nouveau.", "de": "Odoo hat diesen Schlüssel abgelehnt. Erstellen Sie einen neuen und fügen Sie ihn erneut ein."},
    "needs_database": {"en": "Type the name of your database too.", "fr": "Saisissez aussi le nom de votre base de données.", "de": "Geben Sie auch den Namen Ihrer Datenbank ein."},
    "odoo_error": {"en": "Odoo answered with an error. Try again in a minute.", "fr": "Odoo a répondu par une erreur. Réessayez dans une minute.", "de": "Odoo hat mit einem Fehler geantwortet. Versuchen Sie es in einer Minute erneut."},
    "too_many": {"en": "Too many tries. Wait a few minutes and start again from your assistant.", "fr": "Trop d’essais. Attendez quelques minutes et recommencez depuis votre assistant.", "de": "Zu viele Versuche. Warten Sie einige Minuten und beginnen Sie erneut in Ihrem Assistenten."},
    "expired": {"en": "This page has expired. Start again from your assistant.", "fr": "Cette page a expiré. Recommencez depuis votre assistant.", "de": "Diese Seite ist abgelaufen. Beginnen Sie erneut in Ihrem Assistenten."},
    "busy": {"en": "This service is busy. Try again in a few minutes.", "fr": "Ce service est occupé. Réessayez dans quelques minutes.", "de": "Dieser Dienst ist ausgelastet. Versuchen Sie es in einigen Minuten erneut."},
    "bad_request": {"en": "This sign-in link is not valid. Start again from your assistant.", "fr": "Ce lien de connexion n’est pas valide. Recommencez depuis votre assistant.", "de": "Dieser Anmeldelink ist ungültig. Beginnen Sie erneut in Ihrem Assistenten."},
    "not_listed": {"en": "This service works only with the servers its operator lists. Ask them to add yours.",
                   "fr": "Ce service ne fonctionne qu’avec les serveurs listés par son exploitant. Demandez-lui d’ajouter le vôtre.",
                   "de": "Dieser Dienst funktioniert nur mit den Servern, die sein Betreiber auflistet. Bitten Sie ihn, Ihren hinzuzufügen."},
    "not_allowed": {"en": "This assistant is not on the approved list.", "fr": "Cet assistant n’est pas sur la liste approuvée.", "de": "Dieser Assistent steht nicht auf der freigegebenen Liste."},
    # Hub (Nextcloud)
    "not_nextcloud": {"en": "This address is not a {app}. Use the address you open {app} with.",
                      "fr": "Cette adresse n’est pas un {app}. Utilisez l’adresse où vous ouvrez {app}.",
                      "de": "Diese Adresse ist kein {app}. Verwenden Sie die Adresse, unter der Sie {app} öffnen."},
    "nc_too_old": {"en": "This {app} is version {detail}. It needs version 28 or newer.",
                   "fr": "{app} est ici en version {detail}. Il faut la version 28 ou plus récente.",
                   "de": "{app} hat hier Version {detail}. Nötig ist Version 28 oder neuer."},
    "maintenance": {"en": "This {app} is being updated. Try again later.", "fr": "{app} est en cours de mise à jour. Réessayez plus tard.",
                    "de": "{app} wird gerade aktualisiert. Versuchen Sie es später erneut."},
    "login_failed": {"en": "{app} did not start the sign-in. Try again in a minute.",
                     "fr": "{app} n’a pas démarré la connexion. Réessayez dans une minute.",
                     "de": "{app} hat die Anmeldung nicht gestartet. Versuchen Sie es in einer Minute erneut."},
    "login_limit": {"en": "Too many tries. Paste an app password instead.",
                    "fr": "Trop d’essais. Collez plutôt un mot de passe d’application.",
                    "de": "Zu viele Versuche. Fügen Sie stattdessen ein App-Passwort ein."},
    "login_expired": {"en": "Access was not granted in time. Try again.", "fr": "L’accès n’a pas été autorisé à temps. Réessayez.",
                      "de": "Der Zugriff wurde nicht rechtzeitig gewährt. Versuchen Sie es erneut."},
    "password_refused": {"en": "{app} refused this user name and app password. Make a new app password and paste it again.",
                         "fr": "{app} a refusé ce nom d’utilisateur et ce mot de passe d’application. Créez-en un nouveau et collez-le à nouveau.",
                         "de": "{app} hat diesen Benutzernamen und dieses App-Passwort abgelehnt. Erstellen Sie ein neues App-Passwort und fügen Sie es erneut ein."},
    "bad_password": {"en": "Paste the whole app password.", "fr": "Collez le mot de passe d’application entier.",
                     "de": "Fügen Sie das ganze App-Passwort ein."},
    "bad_user": {"en": "Type your user name.", "fr": "Saisissez votre nom d’utilisateur.", "de": "Geben Sie Ihren Benutzernamen ein."},
    "server_error": {"en": "{app} answered with an error. Try again in a minute.", "fr": "{app} a répondu par une erreur. Réessayez dans une minute.",
                     "de": "{app} hat mit einem Fehler geantwortet. Versuchen Sie es in einer Minute erneut."},
    # kSuite (Infomaniak)
    "bad_token": {"en": "Paste the whole token, without spaces.", "fr": "Collez le token entier, sans espaces.",
                  "de": "Fügen Sie das ganze Token ohne Leerzeichen ein."},
    "token_refused": {"en": "Infomaniak refused this token. Make a new one and paste it again.",
                      "fr": "Infomaniak a refusé ce token. Créez-en un nouveau et collez-le à nouveau.",
                      "de": "Infomaniak hat dieses Token abgelehnt. Erstellen Sie ein neues und fügen Sie es erneut ein."},
    "missing_scope": {"en": "This token cannot open {part}. Make a new token with {scope} and paste it again.",
                      "fr": "Ce token ne peut pas ouvrir {part}. Créez un nouveau token avec {scope} et collez-le à nouveau.",
                      "de": "Dieses Token kann {part} nicht öffnen. Erstellen Sie ein neues Token mit {scope} und fügen Sie es erneut ein."},
    "no_mailbox": {"en": "This Infomaniak account has no mailbox this token can open.",
                   "fr": "Ce compte Infomaniak n’a aucune boîte mail que ce token puisse ouvrir.",
                   "de": "Dieses Infomaniak-Konto hat kein Postfach, das dieses Token öffnen kann."},
    "which_mailbox": {"en": "This token opens several mailboxes and none is this account’s own. Make the token while signed in as the mailbox’s owner.",
                      "fr": "Ce token ouvre plusieurs boîtes mail et aucune n’est celle de ce compte. Créez le token en étant connecté avec le compte de la boîte.",
                      "de": "Dieses Token öffnet mehrere Postfächer, aber keines gehört diesem Konto. Erstellen Sie das Token, angemeldet als Inhaber des Postfachs."},
    "account_not_listed": {"en": "This service works only with the mail domains its operator lists. Ask them to add yours.",
                           "fr": "Ce service ne fonctionne qu’avec les domaines de messagerie listés par son exploitant. Demandez-lui d’ajouter le vôtre.",
                           "de": "Dieser Dienst funktioniert nur mit den Mail-Domains, die sein Betreiber auflistet. Bitten Sie ihn, Ihre hinzuzufügen."},
    "ksuite_unreachable": {"en": "Infomaniak did not answer. Try again in a minute.", "fr": "Infomaniak n’a pas répondu. Réessayez dans une minute.",
                           "de": "Infomaniak hat nicht geantwortet. Versuchen Sie es in einer Minute erneut."},
    "ksuite_error": {"en": "Infomaniak answered with an error. Try again in a minute.", "fr": "Infomaniak a répondu par une erreur. Réessayez dans une minute.",
                     "de": "Infomaniak hat mit einem Fehler geantwortet. Versuchen Sie es in einer Minute erneut."},
    "ksuite_busy": {"en": "Infomaniak received too many requests. Wait a minute and try again.",
                    "fr": "Infomaniak a reçu trop de demandes. Attendez une minute et réessayez.",
                    "de": "Infomaniak hat zu viele Anfragen erhalten. Warten Sie eine Minute und versuchen Sie es erneut."},
}

# kSuite: the parts a token can lack, in the words people read, and the scope that opens each one.
KSUITE_PARTS: dict[str, tuple[dict[str, str], str]] = {
    "profile": ({"en": "your profile", "fr": "votre profil", "de": "Ihr Profil"}, "user_info"),
    "mail": ({"en": "Mail", "fr": "Mail", "de": "Mail"}, "workspace:mail"),
    "calendar": ({"en": "Calendar", "fr": "Calendrier", "de": "Kalender"}, "workspace:calendar"),
}
_AND = {"en": "and", "fr": "et", "de": "und"}


def _joined(items: list[str], lang: str) -> str:
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])} {_AND.get(lang, 'and')} {items[-1]}"


def _missing_scope_words(detail: str, lang: str) -> dict[str, str]:
    """``detail`` names the missing parts (e.g. ``mail,calendar``); unknown names are dropped, never shown."""
    names = [n for n in KSUITE_PARTS if n in detail.split(",")] or list(KSUITE_PARTS)
    return {"part": _joined([KSUITE_PARTS[n][0].get(lang) or KSUITE_PARTS[n][0]["en"] for n in names], lang),
            "scope": _joined([KSUITE_PARTS[n][1] for n in names], lang)}


# An error whose words take more than {app} and {detail}: the code's filler builds them from the detail.
ERROR_FILLERS = {"missing_scope": _missing_scope_words}

# The example inside the address box, per app (DESIGN.md: examples live inside the boxes).
PLACEHOLDERS = {"odoo": "erp.example.com", "nextcloud": "cloud.example.com", "ksuite": "example.com"}

APP_NAMES = {"odoo": "Odoo", "nextcloud": "Hub", "ksuite": "kSuite"}

# One accent per app (DESIGN.md): (accent, circle, letter on circle, light-mode line/focus, focus halo)
ACCENTS = {
    "odoo": ("#5B4FD8", "#5B4FD8", "#FFFFFF", "#5B4FD8", "#7468E3"),
    "nextcloud": ("#3FA080", "#3FA080", "#101116", "#3FA080", "#3FA080"),
    "ksuite": ("#E0B84A", "#C99A2E", "#101116", "#9C7A1E", "#E0B84A"),
}

# The connect window (DESIGN.md). Dark is the approved default; light follows the system setting.
CSS = """
:root{--page:#101116;--card:#181B20;--inset:#1F242B;--text:#ECEDEF;--muted:#9BA0A8;--foot:#8F959E;--line:#2A2F36;
--blue:#5B97F5;--btn-text:#101116;--link:#5B97F5;--green:#6FD38A;--orange:#F0A24A}
@media (prefers-color-scheme:light){:root{--page:#F2F3F5;--card:#FFFFFF;--inset:#F2F4F7;--text:#15181D;--muted:#5B616B;
--foot:#6B717B;--line:#DDE1E6;--link:#2F6FD6;--green:#1E7B45;--orange:#A85A10;--accent:var(--accent-light)}}
*{box-sizing:border-box}html{color-scheme:dark light}
body{margin:0;background:var(--page);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Inter,Roboto,sans-serif}
.wrap{max-width:26rem;margin:8vh auto 2rem;padding:0 1rem}
.card{background:var(--card);border:1px solid var(--line);border-top:3px solid var(--accent);border-radius:14px;padding:1.5rem 1.6rem 1.15rem}
.head{display:flex;align-items:center;gap:.65rem;color:var(--muted);font-size:.95rem}
.circle{flex:none;width:2rem;height:2rem;border-radius:50%;background:var(--circle);color:var(--circle-ink);display:grid;place-items:center;font-weight:700;font-size:.95rem}
h1{font-size:1.3rem;line-height:1.3;font-weight:700;margin:1rem 0 .45rem}h1.ok{color:var(--green)}h1.fail{color:var(--orange)}
p{margin:.35rem 0}.lead{color:var(--muted)}.small{color:var(--muted);font-size:.875rem;margin-top:.8rem}
.connecting{display:flex;justify-content:space-between;align-items:baseline;gap:1rem;margin:.6rem 0 .2rem;color:var(--muted)}
.connecting b{color:var(--text)}.connecting button,.linkbtn{all:unset;color:var(--link);cursor:pointer}
.next{background:var(--inset);border-radius:10px;padding:.85rem 1rem;margin:.9rem 0 .4rem}.next b{display:block;margin-bottom:.15rem}
.next ol{margin:0;padding-left:1.2rem}.next li{margin:.2rem 0}a{color:var(--link);text-decoration:none}
label{display:block;margin:1rem 0 .4rem;color:var(--text)}
input{width:100%;font:inherit;padding:.7rem .8rem;border:1px solid var(--line);border-radius:8px;background:var(--inset);color:var(--text)}
input::placeholder{color:var(--muted)}input:focus{outline:none;border:2px solid var(--accent);padding:calc(.7rem - 1px) calc(.8rem - 1px);box-shadow:0 0 0 3px color-mix(in srgb,var(--halo) 40%,transparent)}
.field-err{color:var(--orange);font-weight:400;margin:.45rem 0 0}
input[aria-invalid="true"],input[aria-invalid="true"]:focus{border:2px solid var(--orange);padding:calc(.7rem - 1px) calc(.8rem - 1px)}
.primary{display:block;width:100%;min-height:44px;margin-top:1.05rem;background:var(--blue);color:var(--btn-text);border:0;border-radius:8px;font:inherit;font-weight:600;cursor:pointer;text-align:center;padding:.7rem}
.notnow{display:block;width:100%;min-height:40px;margin-top:.35rem;background:none;border:0;color:var(--muted);font:inherit;cursor:pointer}
.langs{display:flex;justify-content:center;gap:1.4rem;margin-top:1rem;font-size:.9rem}.langs a{color:var(--muted)}.langs a.on{color:var(--text);font-weight:600}
.foot{text-align:center;color:var(--foot);font-size:.78rem;margin-top:.55rem}.foot a{color:var(--foot)}
@media(max-width:480px){.wrap{margin:1rem auto;padding:0 1rem}.card{padding:1.25rem 1.1rem 1rem}}
"""


def pick_language(accept_language: str | None) -> str:
    for part in (accept_language or "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in LANGS:
            return code
    return "en"


def t(key: str, lang: str, **kw: str) -> str:
    text = T[key].get(lang) or T[key]["en"]
    return text.format(**{k: v for k, v in kw.items()})


def error_text(code: str, lang: str, app: str, detail: str = "") -> str:
    entry = ERRORS.get(code) or ERRORS["bad_request"]
    filler = ERROR_FILLERS.get(code) if code in ERRORS else None
    extra = filler(detail, lang) if filler else {}
    return (entry.get(lang) or entry["en"]).format(app=APP_NAMES.get(app, app), detail=detail, **extra)


def e(value: object) -> str:
    return html.escape(str(value), quote=True)


@dataclass
class Asking:
    verified_name: str | None
    claimed_name: str
    return_host: str

    @property
    def name(self) -> str:
        return self.verified_name or self.claimed_name or "?"


# Set once at start from the settings: the publisher's label (D386) and optional extra stylesheets.
MAKER: dict[str, str] = {"label": "open source"}
BRAND: dict = {"styles": (), "logo": "", "name": ""}


def _shell(*, lang: str, app: str, asking: Asking | None, title: str, inner: str, source_url: str,
           flow_id: str | None = None, refresh_to: str | None = None) -> str:
    """The connect window: card with the app's accent line, the assistant · app head, the step, languages, foot."""
    app_name = APP_NAMES.get(app, app)
    accent, circle, ink, light, halo = ACCENTS.get(app, ACCENTS["odoo"])
    # The light-mode line/focus colour must come after the app's accent, or the accent wins (kSuite's honey on white).
    vars_ = (f":root{{--accent:{accent};--circle:{circle};--circle-ink:{ink};--accent-light:{light};--halo:{halo}}}"
             "@media (prefers-color-scheme:light){:root{--accent:var(--accent-light)}}")
    extra = "".join(f'<link rel="stylesheet" href="/brand/{e(n)}">' for n in BRAND["styles"])
    head = ""
    if asking is not None:
        letter = (asking.name.strip()[:1] or "?").upper()
        head = (f'<div class="head"><span class="circle" aria-hidden="true">{e(letter)}</span>'
                f'<span>{e(asking.name)} · {e(app_name)}</span></div>')
    langs = ""
    if flow_id:
        langs = '<nav class="langs" aria-label="Language">' + "".join(
            f'<a href="/connect/{e(flow_id)}?lang={code}"{" class=\"on\" aria-current=\"true\"" if code == lang else ""}>{e(name)}</a>'
            for code, name in LANG_NAMES.items()) + "</nav>"
    foot = (f'<p class="foot">{e(t("connector", lang, app=app_name))} · '
            f'<a href="{e(source_url)}" rel="noopener">{e(MAKER["label"])}</a></p>')
    refresh = f'<meta http-equiv="refresh" content="3;url={e(refresh_to)}">' if refresh_to else ""
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer">'
            f'{refresh}<title>{e(title)}</title><style>{CSS}{vars_}</style>{extra}</head><body>'
            f'<main class="wrap"><section class="card">{head}{inner}{langs}{foot}</section></main></body></html>')


def human_date(ts: int, lang: str, now: float | None = None) -> str:
    """'14 Nov' this year, '14 Nov 2027' another year (used by reminders in the assistant, never on these pages)."""
    import datetime as _dt
    import time as _time
    months = {"en": "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(),
              "fr": "janv. févr. mars avr. mai juin juil. août sept. oct. nov. déc.".split(),
              "de": "Jan. Feb. März Apr. Mai Juni Juli Aug. Sep. Okt. Nov. Dez.".split()}
    d = _dt.datetime.fromtimestamp(ts, _dt.timezone.utc)
    this_year = _dt.datetime.fromtimestamp(_time.time() if now is None else now, _dt.timezone.utc).year
    text = f"{d.day} {months.get(lang, months['en'])[d.month - 1]}"
    return text if d.year == this_year else f"{text} {d.year}"


def _hidden(csrf: str) -> str:
    return f'<input type="hidden" name="csrf" value="{e(csrf)}">'


def _not_now(flow_id: str, csrf: str, lang: str) -> str:
    return (f'<form method="post" action="/connect/{e(flow_id)}/cancel">{_hidden(csrf)}'
            f'<button class="notnow" type="submit">{e(t("not.now", lang))}</button></form>')


def _lead(asking: Asking, lang: str, app_name: str) -> str:
    if asking.verified_name:
        return f'<p class="lead">{e(t("lead", lang, assistant=asking.verified_name, app=app_name))}</p>'
    return f'<p class="lead">{e(t("wants.unverified", lang, name=asking.claimed_name, host=asking.return_host, app=app_name))}</p>'


def server_page(*, lang: str, app: str, flow_id: str, csrf: str, asking: Asking, source_url: str,
                fixed_host: str | None = None, value: str = "", error: str = "") -> str:
    """Step 1 · Address."""
    app_name = APP_NAMES.get(app, app)
    title = t("title.connect", lang, app=app_name, assistant=asking.name)
    field_error = f'<p class="field-err" id="address-err" role="alert">{e(error)}</p>' if error else ""
    described = ' aria-describedby="address-err" aria-invalid="true"' if error else ""
    inner = (f"<h1>{e(title)}</h1>{_lead(asking, lang, app_name)}"
             f'<form method="post" action="/connect/{e(flow_id)}/server">{_hidden(csrf)}'
             f'<label for="address">{e(t("address.label", lang, app=app_name))}</label>'
             f'<input id="address" name="address" type="text" inputmode="url" autocomplete="url" autocapitalize="none" '
             f'spellcheck="false" required placeholder="{e(PLACEHOLDERS.get(app, PLACEHOLDERS["odoo"]))}" value="{e(value or fixed_host or "")}"{described}>'
             f'{field_error}<button class="primary" type="submit">{e(t("continue", lang))}</button></form>'
             + _not_now(flow_id, csrf, lang))
    return _shell(lang=lang, app=app, asking=asking, title=title, inner=inner, source_url=source_url, flow_id=flow_id)


def key_page(*, lang: str, app: str, flow_id: str, csrf: str, asking: Asking, source_url: str, host: str,
             open_url: str, online_hint: bool, ask_database: bool, database: str = "", error: str = "",
             can_change: bool = True) -> str:
    """Step 2 · Key."""
    app_name = APP_NAMES.get(app, app)
    title = t("title.connect", lang, app=app_name, assistant=asking.name)
    change = (f'<button type="submit" form="change">{e(t("change", lang))}</button>' if can_change else "")
    inner = (f"<h1>{e(title)}</h1>"
             + ("" if asking.verified_name else _lead(asking, lang, app_name))
             + f'<p class="connecting"><span>{e(t("connecting", lang))} <b>{e(host)}</b></span>{change}</p>'
             + (f'<form id="change" method="post" action="/connect/{e(flow_id)}/change">{_hidden(csrf)}</form>' if can_change else "")
             + f'<div class="next"><b>{e(t("next", lang))}</b><ol>'
             f'<li><a href="{e(open_url)}" target="_blank" rel="noopener noreferrer">{e(t("step.open", lang, app=app_name))} ↗</a></li>'
             f'<li>{e(t("step.add." + app, lang))}</li><li>{e(t("step.paste", lang))}</li></ol></div>'
             + (f'<p class="small">{e(t("key.online." + app, lang))}</p>' if online_hint else "")
             + f'<form method="post" action="/connect/{e(flow_id)}/key">{_hidden(csrf)}'
             f'<label for="key">{e(t("key.label", lang))}</label>'
             f'<input id="key" name="key" type="password" autocomplete="off" autocapitalize="none" spellcheck="false" required '
             f'maxlength="512" placeholder="{e(t("key.placeholder", lang))}">'
             + (f'<label for="database">{e(t("db.label", lang))}</label><input id="database" name="database" type="text" '
                f'autocomplete="off" autocapitalize="none" spellcheck="false" required value="{e(database)}">' if ask_database else "")
             + (f'<p class="field-err" role="alert">{e(error)}</p>' if error else "")
             + f'<button class="primary" type="submit">{e(t("connect", lang))}</button></form>'
             + _not_now(flow_id, csrf, lang))
    return _shell(lang=lang, app=app, asking=asking, title=title, inner=inner, source_url=source_url, flow_id=flow_id)


def _connecting(flow_id: str, csrf: str, host: str, lang: str, can_change: bool) -> str:
    """"Connecting to {host} · Change" (Change absent when the address was fixed by the connector address)."""
    change = f'<button type="submit" form="change">{e(t("change", lang))}</button>' if can_change else ""
    return (f'<p class="connecting"><span>{e(t("connecting", lang))} <b>{e(host)}</b></span>{change}</p>'
            + (f'<form id="change" method="post" action="/connect/{e(flow_id)}/change">{_hidden(csrf)}</form>' if can_change else ""))


def _next_box(lang: str, open_href: str, app_name: str, steps: list[str], open_text: str | None = None) -> str:
    """The grey Next: box: 1. Open {App} ↗ (new tab), then the app's own steps."""
    opener = open_text or t("step.open", lang, app=app_name)
    return (f'<div class="next"><b>{e(t("next", lang))}</b><ol>'
            f'<li><a href="{e(open_href)}" target="_blank" rel="noopener noreferrer">{e(opener)} ↗</a></li>'
            + "".join(f"<li>{e(step)}</li>" for step in steps) + "</ol></div>")


def grant_page(*, lang: str, app: str, flow_id: str, csrf: str, asking: Asking, source_url: str, host: str,
               login_url: str, can_change: bool = True) -> str:
    """Step 2 · Grant access in the app's own window; this page refreshes itself (no script) while waiting."""
    app_name = APP_NAMES.get(app, app)
    title = t("title.connect", lang, app=app_name, assistant=asking.name)
    inner = (f"<h1>{e(title)}</h1>"
             + ("" if asking.verified_name else _lead(asking, lang, app_name))
             + _connecting(flow_id, csrf, host, lang, can_change)
             + _next_box(lang, login_url, app_name, [t("step.grant." + app, lang), t("step.back", lang)])
             + f'<p class="small"><a href="/connect/{e(flow_id)}?how=paste">{e(t("paste.instead", lang))}</a></p>'
             + _not_now(flow_id, csrf, lang))
    return _shell(lang=lang, app=app, asking=asking, title=title, inner=inner, source_url=source_url,
                  flow_id=flow_id, refresh_to=f"/connect/{flow_id}")


def paste_page(*, lang: str, app: str, flow_id: str, csrf: str, asking: Asking, source_url: str, host: str,
               open_url: str, user: str = "", error: str = "", can_change: bool = True) -> str:
    """Step 2, fallback · paste a user name and an app password made in the app."""
    app_name = APP_NAMES.get(app, app)
    title = t("title.connect", lang, app=app_name, assistant=asking.name)
    inner = (f"<h1>{e(title)}</h1>"
             + ("" if asking.verified_name else _lead(asking, lang, app_name))
             + _connecting(flow_id, csrf, host, lang, can_change)
             + _next_box(lang, open_url, app_name, [t("step.add." + app, lang), t("step.paste." + app, lang)])
             + f'<form method="post" action="/connect/{e(flow_id)}/key">{_hidden(csrf)}'
             f'<label for="user">{e(t("user.label", lang))}</label>'
             f'<input id="user" name="user" type="text" autocomplete="username" autocapitalize="none" spellcheck="false" '
             f'required maxlength="255" placeholder="{e(t("user.placeholder", lang))}" value="{e(user)}">'
             f'<label for="key">{e(t("password.label", lang))}</label>'
             f'<input id="key" name="key" type="password" autocomplete="off" autocapitalize="none" spellcheck="false" required '
             f'maxlength="512" placeholder="{e(t("password.placeholder", lang))}">'
             + (f'<p class="field-err" role="alert">{e(error)}</p>' if error else "")
             + f'<button class="primary" type="submit">{e(t("connect", lang))}</button></form>'
             + _not_now(flow_id, csrf, lang))
    return _shell(lang=lang, app=app, asking=asking, title=title, inner=inner, source_url=source_url, flow_id=flow_id)


def token_page(*, lang: str, app: str, flow_id: str, csrf: str, asking: Asking, source_url: str, open_url: str,
               error: str = "") -> str:
    """Step 2 for an app with no address step (kSuite): open the app's token page, make a token, paste it.

    No "Connecting to … · Change" line: there is no server to pick and nothing to change.
    """
    app_name = APP_NAMES.get(app, app)
    title = t("title.connect", lang, app=app_name, assistant=asking.name)
    inner = (f"<h1>{e(title)}</h1>{_lead(asking, lang, app_name)}"
             + _next_box(lang, open_url, app_name, [t("step.add." + app, lang), t("step.paste." + app, lang)],
                         open_text=t("step.open." + app, lang))
             + f'<form method="post" action="/connect/{e(flow_id)}/key">{_hidden(csrf)}'
             f'<label for="key">{e(t("token.label", lang))}</label>'
             f'<input id="key" name="key" type="password" autocomplete="off" autocapitalize="none" spellcheck="false" required '
             f'maxlength="512" placeholder="{e(t("token.placeholder", lang))}">'
             + (f'<p class="field-err" role="alert">{e(error)}</p>' if error else "")
             + f'<button class="primary" type="submit">{e(t("connect", lang))}</button></form>'
             + _not_now(flow_id, csrf, lang))
    return _shell(lang=lang, app=app, asking=asking, title=title, inner=inner, source_url=source_url, flow_id=flow_id)


def connected_page(*, lang: str, app: str, asking: Asking, source_url: str, who: str, back_url: str) -> str:
    """Step 3 · Connected (green). Returns to the assistant by itself after a moment."""
    app_name = APP_NAMES.get(app, app)
    title = t("ok.title", lang, app=app_name, assistant=asking.name)
    inner = (f'<h1 class="ok">{e(title)}</h1>'
             + (f'<p>{e(t("ok.who", lang, who=who))}</p>' if who else "")
             + f'<p>{e(t("ok.back", lang, assistant=asking.name))}</p>'
             f'<p class="small">{e(t("ok.wrong." + app if "ok.wrong." + app in T else "ok.wrong", lang, app=app_name))}</p>'
             f'<a class="primary" href="{e(back_url)}">{e(t("back.to", lang, assistant=asking.name))}</a>')
    return _shell(lang=lang, app=app, asking=asking, title=title, inner=inner, source_url=source_url, refresh_to=back_url)


def failed_page(*, lang: str, app: str, flow_id: str | None, csrf: str, asking: Asking | None, source_url: str,
                reason: str, retry_url: str | None) -> str:
    """Step 4 · Didn't connect (orange). Nothing was changed."""
    app_name = APP_NAMES.get(app, app)
    title = t("fail.title", lang, app=app_name)
    inner = (f'<h1 class="fail">{e(title)}</h1><p class="lead">{e(reason)}</p>'
             + (f'<a class="primary" href="{e(retry_url)}">{e(t("try.again", lang))}</a>' if retry_url else "")
             + (_not_now(flow_id, csrf, lang) if flow_id else ""))
    return _shell(lang=lang, app=app, asking=asking, title=title, inner=inner, source_url=source_url, flow_id=flow_id)


def allow_page(*, lang: str, app: str, flow_id: str, csrf: str, asking: Asking, source_url: str, host: str, who: str) -> str:
    """Only for an assistant we cannot verify: one explicit yes."""
    app_name = APP_NAMES.get(app, app)
    title = t("allow.title", lang, name=asking.claimed_name)
    inner = (f"<h1>{e(title)}</h1>{_lead(asking, lang, app_name)}"
             f'<p class="lead">{e(t("allow.line", lang, app=app_name, who=who))}</p>'
             f'<form method="post" action="/connect/{e(flow_id)}/allow">{_hidden(csrf)}'
             f'<button class="primary" type="submit">{e(t("allow", lang))}</button></form>'
             + _not_now(flow_id, csrf, lang))
    return _shell(lang=lang, app=app, asking=asking, title=title, inner=inner, source_url=source_url, flow_id=flow_id)


def error_page(*, lang: str, app: str, message: str, source_url: str) -> str:
    """A link that cannot be used any more (expired, invalid): the orange step, no retry."""
    return failed_page(lang=lang, app=app, flow_id=None, csrf="", asking=None, source_url=source_url,
                       reason=message, retry_url=None)
