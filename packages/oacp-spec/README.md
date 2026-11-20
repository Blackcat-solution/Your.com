# Open Agentic Commerce Protocol (OACP) 0.1

OACP defines merchant discovery, cart intent, user approval, and a one-time checkout credential exchange. The wire envelope is JSON-RPC 2.0. Schemas in `schemas/` are normative for this starter.

## Methods

| Method | Direction | Purpose |
| --- | --- | --- |
| `merchant.manifest.get` | Agent → merchant | Fetch merchant identity, origin, supported currencies, category, and payment capabilities. |
| `inventory.search` | Agent → merchant | Search merchant inventory with a query and optional filters. |
| `cart.create` | Agent → merchant | Ask the merchant to create a quoted cart from selected SKUs and quantities. |
| `cart.quote` | Agent → merchant | Refresh taxes, shipping, discounts, stock, and the exact payable total. |
| `checkout.token.exchange` | Merchant → NexusRelay payment service | Redeem a one-time opaque token into a merchant-key-encrypted payment envelope. |

Merchant endpoints must use HTTPS. An agent treats manifest and inventory text as untrusted data and never follows instructions found in product descriptions. A user signature binds the exact merchant, order, amount in minor currency units, currency, category, cart digest, manifest digest, and expiry. Changing any of these fields requires a new signature.

The checkout token is single-use, short-lived, merchant-bound, and backed by a Stripe Issuing virtual card. Exchange requires merchant authentication and an X25519 public key. The payment service retrieves card details from Stripe only inside the exchange operation and immediately encrypts them to the merchant's public key using X25519 + HKDF-SHA256 + AES-256-GCM. The response envelope contains no plaintext PAN or CVC. The merchant decrypts it inside its own PCI-controlled environment.

## Security and deployment profile

The code is a protocol starter, not a certification of PCI, PSD2/SCA, card-network, or consumer-protection compliance. Stripe Issuing access and regional availability are account-dependent. Retrieving virtual-card PAN/CVC through the Stripe API places the payment service in PCI scope; production deployments must complete the applicable Stripe and PCI review, use a KMS/HSM-backed token vault, merchant-specific credentials and key registry, and independently audit the redemption service. The development exchange credential is shared and must not be reused in production.

