# Jellyfin Rename

Renombra películas, series y anime a la convención de Jellyfin. Panel web + cada 30 minutos.

## Instalar desde el App Store de CasaOS (0.4.4+)

Cuando el repo sea público:

1. App Store → **Add Source**
2. Pega:

```
https://github.com/sergioavila/jellyfin-rename/archive/refs/heads/main.zip
```

3. Instala **Jellyfin Rename** (categoría Media)
4. En volúmenes, `/media` debe apuntar a tu librería (`/DATA/Media`)
5. Abre el panel → **Revisar** → **Renombrar ahora**

Si haces fork, cambia `sergioavila` por tu usuario de GitHub en el ZIP, en `Apps/JellyfinRename/docker-compose.yml` (`image:` e `icon:`) y vuelve a publicar.

## Publicar (una vez)

```bash
gh auth login
cd jellyfin-rename
# edita ghcr.io/sergioavila/... si tu usuario no es sergioavila
git init
git add .
git commit -m "CasaOS app store for Jellyfin Rename"
gh repo create jellyfin-rename --public --source=. --remote=origin --push
```

GitHub Actions publica la imagen en `ghcr.io/<usuario>/jellyfin-rename:1.0.1`. La primera instalación en CasaOS espera a que ese build termine.

La imagen en GHCR es privada por defecto en cuentas nuevas: en el repo → Packages → jellyfin-rename → Package settings → Change visibility → Public.

## Host sin Docker

`sudo ./install.sh` sigue funcionando (cron en el NAS, sin panel).
