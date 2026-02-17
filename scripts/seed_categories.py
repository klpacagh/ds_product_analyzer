"""Seed the categories table with dropshipping-relevant categories and keywords."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ds_product_analyzer.db.models import Base, Category

CATEGORIES = {
    "electronics": [
        "wireless earbuds", "portable charger", "smart watch", "phone case",
        "LED strip lights", "bluetooth speaker", "ring light", "USB hub",
        "webcam", "power bank", "phone mount", "cable organizer",
        "screen protector", "laptop stand", "gaming mouse", "smart plug",
        "dash cam", "action camera", "VR headset", "drone",
    ],
    "home-kitchen": [
        "air fryer", "ice maker", "robot vacuum", "electric kettle",
        "sous vide", "instant pot", "water bottle", "tumbler",
        "kitchen gadget", "organizer bin", "LED mirror", "humidifier",
        "electric blanket", "weighted blanket", "shower head", "bidet",
        "smart thermostat", "air purifier", "night light", "desk lamp",
    ],
    "beauty-personal-care": [
        "face serum", "hair oil", "skincare tool", "LED face mask",
        "teeth whitening", "jade roller", "gua sha", "hair clip",
        "scalp massager", "electric toothbrush", "perfume dupe",
        "lip gloss", "sunscreen", "retinol", "vitamin C serum",
        "hair mask", "dry shampoo", "nail lamp", "lash serum",
        "makeup brush set",
    ],
    "fitness-outdoors": [
        "resistance bands", "yoga mat", "massage gun", "foam roller",
        "jump rope", "pull up bar", "kettlebell", "gym bag",
        "running shoes", "fitness tracker", "protein shaker",
        "hiking backpack", "camping gear", "water filter", "headlamp",
        "insulated bottle", "compression socks", "grip strength trainer",
        "ab roller", "exercise bike",
    ],
    "pet-supplies": [
        "dog bed", "cat tree", "pet camera", "automatic feeder",
        "dog harness", "pet grooming", "cat litter", "dog toy",
        "pet water fountain", "calming bed", "slow feeder bowl",
        "pet carrier", "dog jacket", "cat scratcher", "fish tank",
        "pet GPS tracker", "flea collar", "puppy pads", "bird feeder",
        "reptile lamp",
    ],
    "toys-gadgets": [
        "fidget toy", "3D pen", "mini projector", "card game",
        "puzzle", "building blocks", "RC car", "slime kit",
        "science kit", "board game", "magnetic tiles", "bubble machine",
        "telescope", "microscope", "coding robot", "art supplies",
        "walkie talkie", "karaoke microphone", "digital camera kids",
        "craft kit",
    ],
}


def main():
    engine = create_engine("sqlite:///./data.db")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        for name, keywords in CATEGORIES.items():
            existing = session.execute(
                select(Category).where(Category.name == name)
            ).scalar_one_or_none()
            if existing:
                existing.seed_keywords = json.dumps(keywords)
                print(f"  Updated: {name} ({len(keywords)} keywords)")
            else:
                session.add(Category(
                    name=name,
                    seed_keywords=json.dumps(keywords),
                    active=True,
                ))
                print(f"  Created: {name} ({len(keywords)} keywords)")
        session.commit()

    print(f"\nSeeded {len(CATEGORIES)} categories.")


if __name__ == "__main__":
    main()
