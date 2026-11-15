# Current Helm redesign brief

Apply the installed UI UX Pro Max skill throughout. Helm should feel like a polished crypto launch and trading terminal, with a strong visual identity, clear navigation, useful information density, and excellent mobile usability.

1. Landing page

Redesign the landing page around attention, product understanding, and conversion.
- Create a stronger opening headline, compelling product visuals, and clear calls to action.
- Replace “Example Token / EXMPL” with “Test Launch / $TEST.” Clearly identify it as a demonstration.
- Use image generation where useful to create original token artwork and visual assets. Render interface text and controls in code so they remain sharp, accessible, and responsive.
- Make the preview demonstrate Helm’s actual launch and management experience.
- Remove fee-focused marketing, pricing tables, and the Pricing navigation item from the landing page. Keep fees clearly disclosed in transaction reviews and relevant terms.
- Add purposeful animations and interactions using appropriate frontend tools such as CSS, SVG, Canvas, or animation libraries. Support reduced motion and maintain fast loading.
- Do not invent user counts, trading results, testimonials, or performance claims.
2. Sign-in and account creation

Redesign both authentication experiences so they feel engaging and consistent with the landing page.
- Use distinctive artwork, an animated product preview, or another useful visual treatment.
- Keep forms easy to complete, with clear validation, password controls, recovery links, and loading/error states.
- Make the primary action immediately visible on mobile and desktop.
- Preserve authentication, recovery, session, and 2FA safeguards.
3. Sidebar and application layout

Fix the sidebar being cut off around Admin and hiding navigation below it.
- Make every navigation item reachable at normal browser zoom and on short screens.
- Use appropriate spacing, grouping, and independent navigation scrolling where needed.
- Keep account controls accessible without covering navigation.
- Remove Networks from the main sidebar and move its useful information into Settings → Network status, or a dedicated status area.
- Remove the standalone Fees & payments page and its sidebar entry.
- Preserve financial records, fee accounting, referral liabilities, transaction receipts, and required failed-fee settlement functionality. Place necessary user-facing details within transaction history or Settings.
- Preserve referral access in an appropriate account area.
- Remove all Automation navigation and entry points.
4. Dashboard

Substantially improve the dashboard’s visual design and usefulness.
- Prioritize portfolio value, balances, active launches, relevant performance, recent activity, and quick actions.
- Improve hierarchy, charts, spacing, and empty states.
- Load balances automatically and show freshness, loading progress, and partial failures clearly.
- Distinguish genuine zero balances from unavailable data.
- Avoid large empty panels or repetitive summary cards.
- Ensure the dashboard contains no automation features or promotions.
5. Launches page and launchpad readiness

Redesign the launches page and simplify its filters.
- Replace the crowded chain buttons with a compact, searchable selector using chain icons and names.
- Improve launch cards, search, status filters, empty states, and launchpad discovery.
- Investigate every launchpad marked Needs setup. Separate missing configuration, incomplete integration, unavailable upstream support, and missing creator-reward collection.
- Complete integrations and configuration that can actually be completed.
- Integrate the other planned launchpads where official deployed infrastructure and supported interfaces are available.
- Keep Elpida readiness tied to verified mainnet availability.
- Verify underlying functionality before changing a launchpad’s status to “Live.”
- For blockers requiring credentials, account access, deployed contracts, or unavailable upstream features, report the precise dependency and continue independent work.
6. Launchpad-specific launch flows

Research each integrated launchpad using its current official documentation and interface. Build a capability matrix before changing the forms.

Helm’s launch flow should support each launchpad’s actual options and behavior through Helm’s own design system.

Investigate, where applicable:
- Supported chains, token standards, metadata, images, banners, and videos.
- Pairing and quote assets, including supported tokenized-stock or other alternative asset pairings.
- Initial buys, funding requirements, liquidity configuration, and graduation behavior.
- Standard versus taxed tokens, independent tax settings, durations, recipients, allocations, and reward modes.
- Creator rewards, vesting, locks, vaults, and post-launch controls.
Show only options supported by the selected launchpad, network, and deployed integration. Avoid forcing every launchpad into the same generic form.

Keep draft saving, validation, transaction previews, cost disclosures, explicit confirmation, failure recovery, and transaction status clear throughout. Verify official contract/program addresses and supported interfaces before enabling execution.

7. Wallets

Redesign the wallets page for clearer balances, folders, and actions.
- Fetch balances automatically on entry and refresh them sensibly without excessive RPC requests.
- Diagnose and fix the NEAR and TRON “Unavailable” balances shown in the screenshots. Address the actual provider, account-state, configuration, or adapter issue.
- Use a searchable chain selector containing both icons and names.
- Improve folder management, filtering, selection, bulk actions, and responsive layouts.
- Preserve accurate balance precision and distinguish spendable funds from amounts reserved for network costs.
- Provide useful retry and partial-result states when providers fail.
- Keep wallet folders available for organization and explicitly confirmed manual operations after automation is removed.
8. Private profile and performance cards

Redesign Profile as a private account and performance area.
- Let every user set a nickname/display name and, if needed, a username.
- Use the chosen display name on their PnL and performance cards.
- Remove social-profile language and public profile discovery/viewing features. Helm is not intended to be a social network.
- Improve the presentation of performance and trading history.
- Clearly distinguish realized PnL, unrealized PnL, net flows, fees, and incomplete valuation data.
- Sharing an exported performance card must not expose a publicly browsable account profile.
Add Settings → Performance cards with:
- Several polished crypto-themed templates.
- Custom background image uploads.
- Short custom video backgrounds, approximately 2–10 MB, with a 10 MB maximum.
- Preview, crop/position, contrast, and layout controls.
- Static-image export, plus animated/video export where implemented and supported.
- A static fallback and reduced-motion behavior.
- Validated media formats, dimensions, duration, file size, and secure private storage.
- Controls to prevent unintended exposure of email addresses, wallet addresses, balances, or other private information.
I will provide additional template references. Build the customization foundation so those templates can be incorporated without delaying unrelated work.

9. Token workspace and trading interface

Redesign the token workspace using established trading terminals as interaction references.
- Make the chart visible immediately when the page opens.
- Remove the large bonding-curve information banner from the top. Move useful protocol details into a compact details section.
- Reduce oversized headings, repeated summaries, and excessive vertical spacing.
- Organize the chart, essential market statistics, manual trading controls, positions, and wallet information clearly.
- Make mobile layouts usable without requiring desktop scaling.
- Preserve quote freshness, slippage limits, price-impact warnings, fee disclosure, and transaction confirmation.
- Remove Automation buttons, tabs, widgets, and promotional content.
10. Remove automation completely from Helm

Remove the automation feature throughout the application.
- Remove all Automation buttons, tabs, sidebar links, pages, onboarding content, and documentation.
- Remove automation API endpoints, scheduled trading plans, execution engines, configuration, and feature-specific dependencies.
- Disable existing plans and cancel queued automation actions so they cannot submit further transactions.
- Reconcile already submitted transactions without resubmitting them. Preserve transaction history, receipts, and financial records.
- Retire stored automation settings through safe migrations without affecting wallets, balances, launches, or unrelated account data.
- Remove DCA, scheduled buys/sells, automated volume generation, and other unattended trading features. Do not replace automation with another trading feature.
- Update tests and verify that retired endpoints, old links, queued jobs, and application restarts cannot reactivate automation.
Keep automatic balance loading, market-data refresh, transaction-confirmation monitoring, and other essential background infrastructure working. Manual trading, explicitly confirmed token distribution, and consolidation remain available.

11. Post-launch wallet attachment, distribution, and consolidation

Improve the launch workspace’s wallet management.
- Allow users to attach an existing single wallet or an eligible wallet folder after launch, even if it was not selected during creation.
- Restrict attachments to authorized wallets on the correct chain and prevent duplicates.
- Make attachment distinct from transferring funds or granting new signing authority.
- Add a developer-wallet Distribute tokens action supporting explicit amounts, percentages, or equal distribution across selected attached wallets.
- Add Consolidate tokens to move selected token holdings from authorized launch wallets into one selected destination.
- Preview recipients, amounts, totals, network costs, and expected remaining balances before confirmation.
- Check token restrictions, transfer taxes, native gas requirements, account creation costs, and chain-specific transfer behavior.
- Track each transfer independently and support safe recovery from partial completion without duplicating confirmed transfers.
- Preserve required authentication and signing controls.
Distribution and consolidation must remain explicitly initiated and confirmed operations, with no recurring schedules or unattended trading behavior.

12. Economics, verification, and delivery

Preserve the agreed economics:
- Launches remain free for every account.
- Helm trading fees remain 2.5%.
- Creator-reward allocation remains 2.5% on new Helm launches, subject to verified integration support.
- Direct referrers earn 30% of actual collected Helm fees.
- Historical immutable contracts retain their original terms.
- Do not introduce a subscription requirement for basic launches.
Removing pricing-focused pages must not remove transaction fee disclosures, accounting, receipts, or referral obligations.

Implement the work in coherent phases, completing and testing each phase. Verify frontend and backend behavior together, including authorization, media uploads, financial calculations, launchpad capabilities, transaction recovery, and complete automation removal.

Test mobile, tablet, desktop, short screens, normal browser zoom, keyboard navigation, reduced motion, and relevant error states. Use isolated fixtures, simulations, and local forks for financial execution tests.

Push the changes, complete CI, deploy in the appropriate order, and verify the actual live site. Finish with a concise report explaining what shipped, what was tested, and any precise remaining blockers. Clearly distinguish completed integrations from features awaiting external infrastructure.
