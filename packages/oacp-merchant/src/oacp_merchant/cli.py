"""Command line entry point for the OACP merchant reference client."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys

import httpx

from oacp_merchant import (
    build_manifest,
    generate_keystore,
    load_keystore,
    redeem_checkout_token,
    register_trust,
    save_keystore,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oacp-merchant",
        description="Sign OACP manifests, register merchant trust and redeem checkout tokens.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    keys = commands.add_parser("keys", help="Generate a merchant keystore (signing, decryption, exchange secret).")
    keys.add_argument("--merchant-id", required=True)
    keys.add_argument("--signing-key-id", required=True)
    keys.add_argument("--out", default="merchant-keys.json")

    manifest = commands.add_parser("manifest", help="Print a signed merchant manifest.")
    manifest.add_argument("--keys", required=True)
    manifest.add_argument("--merchant-name", required=True)
    manifest.add_argument("--origin", required=True)
    manifest.add_argument("--checkout-endpoint", required=True)
    manifest.add_argument("--currency", default="usd")
    manifest.add_argument("--category", required=True, help="Stripe Issuing merchant category, e.g. computer_software_stores.")

    trust = commands.add_parser("trust", help="Register the merchant with the payment service.")
    trust.add_argument("--keys", required=True)
    trust.add_argument("--api-url", default="http://localhost:8000")
    trust.add_argument("--origin", required=True)
    trust.add_argument("--admin-token", default=os.environ.get("OACP_ADMIN_TOKEN", ""))

    redeem = commands.add_parser("redeem", help="Redeem a checkout token and decrypt the card envelope.")
    redeem.add_argument("--keys", required=True)
    redeem.add_argument("--api-url", default="http://localhost:8000")
    redeem.add_argument("--token", required=True)
    redeem.add_argument("--idempotency-key", default=f"merchant-{secrets.token_hex(8)}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.command == "keys":
        keystore = generate_keystore(merchant_id=args.merchant_id, signing_key_id=args.signing_key_id)
        path = save_keystore(args.out, keystore)
        print(
            json.dumps(
                {
                    "keystore": str(path),
                    "merchant_id": keystore["merchant_id"],
                    "signing_key_id": keystore["signing_key_id"],
                    "ed25519_public_key": keystore["ed25519_public_key"],
                    "x25519_public_key": keystore["x25519_public_key"],
                },
                indent=2,
            )
        )
        print("Keep this file private: it holds the signing key and the exchange credential.", file=sys.stderr)
        return 0

    keystore = load_keystore(args.keys)

    if args.command == "manifest":
        manifest = build_manifest(
            merchant_id=keystore["merchant_id"],
            merchant_name=args.merchant_name,
            origin=args.origin,
            checkout_endpoint=args.checkout_endpoint,
            currency=args.currency,
            mcc_category=args.category,
            signing_key_id=keystore["signing_key_id"],
            ed25519_private_key=keystore["ed25519_private_key"],
        )
        print(json.dumps(manifest, indent=2))
        return 0

    try:
        if args.command == "trust":
            if not args.admin_token:
                print("Set OACP_ADMIN_TOKEN or pass --admin-token.", file=sys.stderr)
                return 2
            result = register_trust(
                api_url=args.api_url,
                merchant_id=keystore["merchant_id"],
                admin_token=args.admin_token,
                origin=args.origin,
                signing_key_id=keystore["signing_key_id"],
                signing_public_key=keystore["ed25519_public_key"],
                exchange_secret=keystore["exchange_secret"],
            )
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "redeem":
            card = redeem_checkout_token(
                api_url=args.api_url,
                merchant_id=keystore["merchant_id"],
                checkout_token=args.token,
                exchange_secret=keystore["exchange_secret"],
                x25519_private_key=keystore["x25519_private_key"],
                idempotency_key=args.idempotency_key,
            )
            print(json.dumps(card, indent=2))
            return 0
    except httpx.HTTPStatusError as exc:
        print(f"Payment service rejected the request: {exc.response.status_code} {exc.response.text}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"Payment service is unreachable: {exc}", file=sys.stderr)
        return 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
