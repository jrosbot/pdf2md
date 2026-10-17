# Manual: credencials OAuth de PDF2MD

Aquest manual explica com crear les tres aplicacions d'inici de sessió que necessita el servidor MCP de PDF2MD: Google, Microsoft i Apple.

Les credencials són de l'aplicació PDF2MD, no dels usuaris. No demanarem ni guardarem la contrasenya de cap usuari. Cada proveïdor et donarà un identificador públic (`client_id`) i un secret que només s'ha de posar al servidor (`client_secret`).

## Abans de començar

Primer has de tenir un domini públic amb HTTPS per al servidor MCP. Substitueix `mcp.example.com` per aquest domini real en tots els passos.

Els callbacks que ha d'acceptar cada proveïdor són exactament:

```text
https://mcp.example.com/oauth/callback/google
https://mcp.example.com/oauth/callback/microsoft
https://mcp.example.com/oauth/callback/apple
```

No afegeixis una barra final, no canviïs el camí i no facis servir `http://` en producció.

Quan ja tinguis les tres credencials, les posaràs al gestor de secrets del desplegament. Les variables que espera PDF2MD són:

```text
PDF2MD_GOOGLE_CLIENT_ID
PDF2MD_GOOGLE_CLIENT_SECRET
PDF2MD_MICROSOFT_CLIENT_ID
PDF2MD_MICROSOFT_CLIENT_SECRET
PDF2MD_APPLE_CLIENT_ID
PDF2MD_APPLE_CLIENT_SECRET
```

No les posis al repositori, al `README.md`, a `mcp.json` ni en cap captura de pantalla.

## 1. Google

Documentació oficial: [OAuth 2.0 per a aplicacions web de servidor](https://developers.google.com/identity/protocols/oauth2/web-server).

### Crear el projecte i la pantalla de consentiment

1. Obre [Google Cloud Console](https://console.cloud.google.com/).
2. Crea un projecte nou, per exemple `PDF2MD`.
3. Ves a **Google Auth Platform** (o **APIs i serveis**) → **Branding / OAuth consent screen**.
4. Configura l'aplicació com a **External**, perquè hi puguin entrar persones fora de la teva organització.
5. Escriu el nom públic `PDF2MD`, el correu de suport i el correu de contacte del desenvolupador.
6. Afegeix només els àmbits d'identitat necessaris: `openid`, `email` i `profile`.
7. Si Google mostra l'aplicació com a **Testing**, afegeix els comptes de prova. Per a ús públic hauràs de completar la publicació/verificació que Google et demani.

### Crear el client web

1. Ves a **Google Auth Platform** → **Clients** → **Create client**.
2. Tria **Web application**.
3. Posa un nom com `PDF2MD MCP production`.
4. A **Authorized redirect URIs**, afegeix exactament:

   ```text
   https://mcp.example.com/oauth/callback/google
   ```

5. Crea el client.
6. Copia el **Client ID** a `PDF2MD_GOOGLE_CLIENT_ID`.
7. Copia el **Client secret** a `PDF2MD_GOOGLE_CLIENT_SECRET`.

El secret només es mostra al servidor; si es filtra, revoca'l i crea'n un de nou.

## 2. Microsoft

Documentació oficial: [Registrar una aplicació a Microsoft Entra ID](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app) i [afegir un redirect URI](https://learn.microsoft.com/en-us/entra/identity-platform/how-to-add-redirect-uri).

### Registrar l'aplicació

1. Obre [Microsoft Entra admin center](https://entra.microsoft.com/).
2. Ves a **Entra ID** → **App registrations** → **New registration**.
3. Nom: `PDF2MD MCP`.
4. A **Supported account types**, tria **Accounts in any organizational directory and personal Microsoft accounts**. Això permet comptes Microsoft personals i comptes de feina/escola.
5. A **Redirect URI**, tria **Web** i introdueix:

   ```text
   https://mcp.example.com/oauth/callback/microsoft
   ```

6. Prem **Register**.
7. A la pantalla **Overview**, copia **Application (client) ID** a `PDF2MD_MICROSOFT_CLIENT_ID`.
8. Copia també el **Directory (tenant) ID** només si més endavant vols limitar l'accés a un tenant concret. El servidor de PDF2MD està preparat per al tenant públic `common` per defecte.

### Crear el secret

1. Dins de l'aplicació, ves a **Certificates & secrets**.
2. A **Client secrets**, prem **New client secret**.
3. Posa una descripció com `PDF2MD production` i una caducitat adequada a la teva política.
4. Prem **Add**.
5. Copia immediatament la columna **Value** a `PDF2MD_MICROSOFT_CLIENT_SECRET`. No copiïs el camp **Secret ID**: no és el secret.

Si el secret caduca, crea'n un de nou abans de l'expiració, actualitza el desplegament i elimina l'antic.

## 3. Apple

Apple necessita una mica més de configuració. El `client_secret` d'Apple no és una cadena que Apple mostri directament: és un JWT signat pel servidor amb una clau privada d'Apple.

Documentació oficial: [Configurar Sign in with Apple per al web](https://developer.apple.com/help/account/capabilities/configure-sign-in-with-apple-for-the-web/), [registrar un Services ID](https://developer.apple.com/help/account/identifiers/register-a-services-id/) i [crear una clau privada](https://developer.apple.com/help/account/capabilities/create-a-sign-in-with-apple-private-key).

### Crear el Services ID

1. Entra a [Apple Developer → Certificates, Identifiers & Profiles](https://developer.apple.com/account/resources/identifiers/list).
2. A **Identifiers**, prem **+** i tria **Services IDs**.
3. Descripció: `PDF2MD MCP`.
4. Identifier: per exemple `com.thediar.pdf2md.web`. Aquest valor serà el `client_id` d'Apple.
5. Activa **Sign in with Apple** i prem **Configure**.
6. Selecciona una **Primary App ID** existent amb Sign in with Apple activat.
7. A **Domains and Subdomains**, introdueix el domini, sense `https://` ni cap camí:

   ```text
   mcp.example.com
   ```

8. A **Return URLs**, introdueix l'URL complet:

   ```text
   https://mcp.example.com/oauth/callback/apple
   ```

9. Desa la configuració.
10. Copia l'identifier del Services ID a `PDF2MD_APPLE_CLIENT_ID`.

### Crear la clau privada

1. A **Keys**, prem **+**.
2. Nom: `PDF2MD Sign in with Apple`.
3. Activa **Sign in with Apple** i associa la clau a la mateixa Primary App ID.
4. Prem **Register** i després **Download**. El fitxer `.p8` només es pot descarregar una vegada.
5. Guarda el fitxer en un gestor de secrets. No el pugis al repositori.
6. Anota el **Key ID** i el **Team ID** del compte d'Apple Developer.

### Generar `PDF2MD_APPLE_CLIENT_SECRET`

El valor final ha de ser un JWT amb aquestes dades:

- `iss`: el teu **Team ID**.
- `iat`: hora actual en segons Unix.
- `exp`: una data futura, normalment com a màxim sis mesos després de `iat`.
- `aud`: `https://appleid.apple.com`.
- `sub`: l'identifier del Services ID, és a dir, el valor de `PDF2MD_APPLE_CLIENT_ID`.
- Capçalera JWT `kid`: el **Key ID**.
- Algoritme: `ES256`.

El JWT s'ha de signar amb la clau privada `.p8`. Guarda el JWT resultant a `PDF2MD_APPLE_CLIENT_SECRET`. No hi posis el contingut del `.p8` directament.

El projecte ja valida aquest JWT quan intercanvia el codi d'autorització amb Apple. Quan s'acosti la caducitat del JWT, genera'n un de nou i reinicia o actualitza el desplegament.

## Posar les credencials al servidor

Al proveïdor on despleguis el contenidor, crea les sis variables com a secrets. Exemple conceptual (no el copiïs a un repositori):

```text
PDF2MD_GOOGLE_CLIENT_ID=...
PDF2MD_GOOGLE_CLIENT_SECRET=...
PDF2MD_MICROSOFT_CLIENT_ID=...
PDF2MD_MICROSOFT_CLIENT_SECRET=...
PDF2MD_APPLE_CLIENT_ID=com.thediar.pdf2md.web
PDF2MD_APPLE_CLIENT_SECRET=eyJ...
```

Comprova també que existeixen aquestes variables generals:

```text
PDF2MD_PUBLIC_BASE_URL=https://mcp.example.com
PDF2MD_ISSUER_URL=https://mcp.example.com
PDF2MD_RESOURCE_URL=https://mcp.example.com/mcp
PDF2MD_SESSION_SECRET=<com a mínim 32 caràcters aleatoris>
PDF2MD_DATABASE_PATH=/data/pdf2md.sqlite3
```

El directori de la base de dades ha de ser persistent. Si es perd, es perden les sessions, les identitats vinculades i el recompte de quota diària.

## Prova final

1. Reinicia el servidor després de guardar els secrets.
2. Obre `https://mcp.example.com/healthz`; ha de respondre `{"status":"ok","service":"pdf2md"}`.
3. Connecta el connector MCP a ChatGPT.
4. Comprova que apareixen els botons Google, Microsoft i Apple.
5. Prova cada proveïdor en una finestra privada del navegador.
6. Adjunta un PDF petit i demana que PDF2MD retorni un `.md`.
7. Confirma que el PDF temporal no queda al disc i que una segona conversió incrementa la quota del mateix correu.

Si un proveïdor falla amb `redirect_uri_mismatch`, `AADSTS50011` o un error de retorn d'Apple, compara caràcter per caràcter l'URL configurat al proveïdor amb el callback corresponent de la secció **Abans de començar**.

