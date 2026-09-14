"""Kildespor — 'source trail' in Norwegian.

A deterministic agent that builds evidence-linked company profiles for
Norwegian organisations from permitted public sources (Brreg open data,
NAV job feed), with strict identity gates on company websites.
"""
from .models import CompanyProfile, Fact, Source
from .pipeline import Pipeline

__all__ = ["CompanyProfile", "Fact", "Pipeline", "Source", "__version__"]
__version__ = "1.0.0"
