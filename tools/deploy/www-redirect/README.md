# Helm www redirect

Deploy these static files to the Vercel project `helm-www-redirect` and assign only
`www.helmterminals.xyz`. Every path redirects permanently to the matching path on
`https://helmterminals.xyz`, where the main frontend is hosted on Railway.

The existing apex/API DNS records and the Elpida and farm projects are independent
of this redirect. This project has no Git connection, dependencies or build step.
