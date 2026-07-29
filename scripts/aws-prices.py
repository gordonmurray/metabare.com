#!/usr/bin/env python3
"""Re-query the eu-west-1 list prices quoted in the README's cost table.

A cost claim should be reproducible and dated. This script is the reproduction
step: run it and compare against the table. Prices change, and a figure quoted
from memory a year later is worse than no figure at all.

    AWS_PROFILE=<profile> uv run --with boto3 python scripts/aws-prices.py

The Price List API is only available in us-east-1 and ap-south-1 regardless of
which region is being priced.
"""

from __future__ import annotations

import json
import sys
from typing import Any

REGION = "eu-west-1"

# (label, service code, filters, usage-type substring to keep)
QUERIES: list[tuple[str, str, dict[str, str], str]] = [
    ("EKS control plane", "AmazonEKS", {"usagetype": "EU-AmazonEKS-Hours:perCluster"}, ""),
    ("NAT Gateway", "AmazonEC2", {"productFamily": "NAT Gateway"}, "NatGateway-"),
    (
        "VPC endpoint (PrivateLink)",
        "AmazonVPC",
        {"productFamily": "VpcEndpoint"},
        "VpcEndpoint-Hours",
    ),
    ("EBS gp3", "AmazonEC2", {"productFamily": "Storage", "volumeApiName": "gp3"}, "VolumeUsage"),
]

INSTANCE_TYPES = ["t3.large", "c7i.large", "m7i.large", "m7i.xlarge"]
HOURS_PER_MONTH = 730


def price_rows(
    client: Any, service: str, filters: dict[str, str], keep: str
) -> set[tuple[str, float, str]]:
    api_filters = [{"Type": "TERM_MATCH", "Field": k, "Value": v} for k, v in filters.items()]
    api_filters.append({"Type": "TERM_MATCH", "Field": "regionCode", "Value": REGION})
    rows: set[tuple[str, float, str]] = set()
    pages = client.get_paginator("get_products").paginate(
        ServiceCode=service, Filters=api_filters, PaginationConfig={"MaxItems": 2000}
    )
    for page in pages:
        for raw in page["PriceList"]:
            product = json.loads(raw)
            usage_type = product["product"]["attributes"].get("usagetype", "")
            if keep and keep not in usage_type:
                continue
            for term in product["terms"].get("OnDemand", {}).values():
                for dimension in term["priceDimensions"].values():
                    rows.add(
                        (usage_type, float(dimension["pricePerUnit"]["USD"]), dimension["unit"])
                    )
    return rows


def main() -> int:
    try:
        import boto3
    except ImportError:
        print("boto3 is required: uv run --with boto3 python scripts/aws-prices.py")
        return 1

    client = boto3.client("pricing", region_name="us-east-1")
    print(f"List prices for {REGION}. Compare against the cost table in the README.\n")

    for label, service, filters, keep in QUERIES:
        print(f"{label}:")
        for usage_type, usd, unit in sorted(price_rows(client, service, filters, keep)):
            hourly = unit.lower() in ("hrs", "hours", "hour", "hourly")
            monthly = f"  (~${usd * HOURS_PER_MONTH:,.2f}/month)" if hourly else ""
            print(f"  {usage_type:<40} ${usd:<12.6f} /{unit}{monthly}")
        print()

    print("EC2 On-Demand, Linux, shared tenancy:")
    for instance_type in INSTANCE_TYPES:
        rows = price_rows(
            client,
            "AmazonEC2",
            {
                "instanceType": instance_type,
                "operatingSystem": "Linux",
                "tenancy": "Shared",
                "preInstalledSw": "NA",
                "capacitystatus": "Used",
            },
            "BoxUsage",
        )
        for usage_type, usd, unit in sorted(rows):
            print(
                f"  {usage_type:<40} ${usd:<12.6f} /{unit}  (~${usd * HOURS_PER_MONTH:,.2f}/month)"
            )

    print("\nList prices only. Excludes VAT, Savings Plans, credits and the free tier.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
