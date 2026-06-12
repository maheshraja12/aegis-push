"""
==============================================================================
Aegis V2 — dummy_app/payment.py
Multi-file payment microservice (target for chaos injection & RAG indexing)
==============================================================================
"""

import logging

logger = logging.getLogger("payment_service")


def calculate_tax(amount: float, tax_rate: float) -> float:
    """
    Calculate tax for a transaction.
    Args:
        amount:   Gross amount.
        tax_rate: Tax rate as a decimal (e.g., 0.1 for 10%).
    Returns:
        The tax amount.
    """
    # This is the CORRECT version — Aegis V1 already fixed this.
    # Chaos Monkey may re-break it; Aegis V2 will fix it again.
    tax = amount * tax_rate
    return tax


def apply_discount(amount: float, discount_pct: float) -> float:
    """
    Apply a percentage discount to an amount.
    Args:
        amount:       Original amount.
        discount_pct: Discount as a percentage (e.g., 10 for 10%).
    Returns:
        Discounted amount.
    """
    if discount_pct < 0 or discount_pct > 100:
        raise ValueError(f"Discount percentage must be between 0 and 100. Got: {discount_pct}")
    discount_multiplier = 1 - (discount_pct / 100)
    return round(amount * discount_multiplier, 2)


def process_transaction(amount: float, currency: str, tax_rate: float) -> dict:
    """
    Process a full payment transaction.
    Args:
        amount:   Gross transaction amount.
        currency: ISO 4217 currency code.
        tax_rate: Tax rate as a decimal.
    Returns:
        Dict with amount, currency, tax, total.
    """
    if amount < 0:
        raise ValueError(f"Amount cannot be negative. Got: {amount}")
    if not currency or len(currency) != 3:
        raise ValueError(f"Invalid currency code: {currency}")

    tax = calculate_tax(amount, tax_rate)
    total = amount + tax

    result = {
        "amount": round(amount, 2),
        "currency": currency.upper(),
        "tax": round(tax, 2),
        "total": round(total, 2),
        "status": "processed",
    }
    logger.info(f"Transaction processed: {result}")
    return result


def validate_card(card_number: str) -> bool:
    """
    Validate a card number using Luhn algorithm.
    Args:
        card_number: Card number as a string (digits only).
    Returns:
        True if valid, False otherwise.
    """
    digits = [int(d) for d in card_number if d.isdigit()]
    if len(digits) < 13:
        return False
    # Luhn algorithm
    total = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0
