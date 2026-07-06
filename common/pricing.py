"""Stima della spesa in Claude a partire dai token effettivamente usati.

Anthropic non espone un endpoint per leggere il saldo prepagato reale con una
API key normale (solo i limiti di richieste/token al minuto, che sono
un'altra cosa — vedi common/claude_client.py). Questo modulo calcola una
spesa STIMATA in base ai prezzi pubblici per milione di token e ai token
effettivamente consumati dalla piattaforma, come sostituto ragionevole.
"""

from __future__ import annotations

# Prezzi pubblici $/1M token (input, output). Aggiornare se cambiano i listini.
_PRICING_PER_MILLION_TOKENS: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
_DEFAULT_PRICING = _PRICING_PER_MILLION_TOKENS["claude-sonnet-5"]

# Approssimazioni standard per la cache prompt: la scrittura costa ~1.25x il
# prezzo dell'input, la lettura da cache ~0.1x.
_CACHE_WRITE_MULTIPLIER = 1.25
_CACHE_READ_MULTIPLIER = 0.1


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """Stima in dollari la spesa per i token indicati, in base al modello.

    Se il modello non è nella tabella prezzi, usa i prezzi di
    claude-sonnet-5 come approssimazione ragionevole (meglio di niente,
    ma segnalata come stima nell'interfaccia)."""
    input_price, output_price = _PRICING_PER_MILLION_TOKENS.get(model, _DEFAULT_PRICING)
    input_unit_price = input_price / 1_000_000
    output_unit_price = output_price / 1_000_000

    cost = input_tokens * input_unit_price
    cost += output_tokens * output_unit_price
    cost += cache_creation_tokens * input_unit_price * _CACHE_WRITE_MULTIPLIER
    cost += cache_read_tokens * input_unit_price * _CACHE_READ_MULTIPLIER
    return cost
