add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header X-Robots-Tag "noindex, nofollow, noarchive" always;
add_header Content-Security-Policy "default-src 'self'; img-src 'self' data: https://api.verdify.ai; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self' https://api.verdify.ai; frame-src 'none'; object-src 'none'; base-uri 'self'; form-action 'self' https://verdify.ai https://api.verdify.ai" always;
