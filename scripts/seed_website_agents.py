import json
from datetime import datetime
from app.core.database import SessionLocal
from app.models.agent import Agent
from app.models.document import Document, DocumentChunk

def seed():
    db = SessionLocal()


    agents_to_create = [
        {
            "name": "Wedeazzy",
            "description": "Wedding planning, venue booking, photography, catering and decor AI assistant for wedeazzy.com",
            "color": "#d9480f",
            "profile": {
                "agent_name": "Riya",
                "company_name": "Wedeazzy",
                "company_tagline": "Complete Wedding Planning & Vendor Management Simplified",
                "website_url": "https://wedeazzy.com",
                "voice_speaker": "priya",
                "default_language": "hi-IN",
                "agent_role": "wedding planner and booking concierge",
                "customer_noun": "couple / wedding family",
                "objective": "Understand the wedding requirements (dates, guest count, city/destination, budget, venues, photography, catering, decor) and book a dedicated wedding planner consultation.",
                "call_to_action": "Schedule a free 1-on-1 wedding consultation with Wedeazzy expert wedding planners.",
                "greeting_en": "Hi {name}, congratulations! This is {agent} from Wedeazzy. Are you planning an upcoming wedding or celebration?",
                "greeting_hi": "नमस्ते {name}, बधाई हो! मैं Wedeazzy से {agent} बोल रही हूँ। क्या आप आगामी शादी या इवेंट के लिए वेडिंग वेन्यू या प्लानर तलाश रहे हैं?",
                "instructions": "Be polite, celebratory, warm and helpful. Understand event dates, destination/city, budget bracket, guest count, and vendor needs. Never quote fixed prices without planner review. Confirm WhatsApp number and schedule a callback or consultation call.",
                "objection_handling": "Budget concerns: Explain customized packages starting from budget-friendly banquet halls to luxury destination resorts. Comparing with others: Wedeazzy provides verified vendors, guaranteed pricing, and dedicated coordinator support. Send brochure: Offer to send complete wedding catalog & venue portfolio on WhatsApp immediately.",
                "qualification_criteria": "Hot: Wedding date finalized with budget and looking for venue/full planning. Warm: Exploring venues/dates for next season. Cold: Just browsing with no event planned.",
                "forbidden_topics": "Never guarantee venue availability or fixed discounts without consulting vendor calendar.",
                "max_call_minutes": 6,
                "record_calls": True,
                "detect_voicemail": False,
                "transfer_number": "",
                "inbound_mode": "ai",
                "transfer_on_request": True,
                "after_hours_mode": "ai",
                "after_hours_message": "",
                "forward_fallback": "ai",
                "notify_missed_calls": True,
                "inbound_collect": ["name", "wedding_date", "city", "guest_count", "budget", "service_type", "phone", "email"],
                "team_members": [],
                "agent_password": ""
            },
            "automation": {
                "auto_dial_enabled": True,
                "speed_to_lead_enabled": True,
                "speed_to_lead_min_seconds": 30,
                "speed_to_lead_max_seconds": 120,
                "calling_hours_start": 9,
                "calling_hours_end": 21,
                "calling_days": [0, 1, 2, 3, 4, 5, 6],
                "max_retries": 3
            }
        },
        {
            "name": "Hotelzz",
            "description": "Hotel reservations, corporate room bookings, banquet halls and stay inquiry assistant for hotelzz",
            "color": "#0e8a5e",
            "profile": {
                "agent_name": "Kavya",
                "company_name": "Hotelzz",
                "company_tagline": "Premium Stays, Luxury Suites & Seamless Bookings",
                "website_url": "https://hotelzz.com",
                "voice_speaker": "priya",
                "default_language": "en-IN",
                "agent_role": "hospitality reservation specialist",
                "customer_noun": "guest / corporate booker",
                "objective": "Assist with hotel stay reservations, room upgrades, group/corporate bookings, amenities, banquet halls, and confirm bookings or schedule manager follow-ups.",
                "call_to_action": "Reserve your stay or connect with our corporate reservations manager for best group rates.",
                "greeting_en": "Hello {name}, thank you for contacting Hotelzz! This is {agent}. How may I assist you with your hotel reservation or stay enquiry today?",
                "greeting_hi": "नमस्ते {name}, Hotelzz में आपका स्वागत है! मैं {agent} बात कर रही हूँ। होटल बुकिंग या स्टे इन्क्वायरी में मैं आपकी क्या मदद कर सकती हूँ?",
                "instructions": "Be courteous, efficient, welcoming, and hospitable. Inquire about check-in/check-out dates, city/property location, number of rooms/guests, room category preference, and any special requests (airport pickup, breakfast, banquet).",
                "objection_handling": "Rate inquiry: Explain best available rate guarantee, complimentary breakfast and flexible cancellation policies. Group booking: Highlight tailored corporate discounts and free banquet quotes.",
                "qualification_criteria": "Hot: Definite travel dates and ready to book. Warm: Comparing hotels for tentative dates. Cold: General enquiry with no travel plan.",
                "forbidden_topics": "Do not offer unauthorized complimentary nights or discounts below standard floor rates.",
                "max_call_minutes": 5,
                "record_calls": True,
                "detect_voicemail": False,
                "transfer_number": "",
                "inbound_mode": "ai",
                "transfer_on_request": True,
                "after_hours_mode": "ai",
                "after_hours_message": "",
                "forward_fallback": "ai",
                "notify_missed_calls": True,
                "inbound_collect": ["name", "check_in_date", "check_out_date", "city", "number_of_rooms", "guests", "phone", "email"],
                "team_members": [],
                "agent_password": ""
            },
            "automation": {
                "auto_dial_enabled": True,
                "speed_to_lead_enabled": True,
                "speed_to_lead_min_seconds": 30,
                "speed_to_lead_max_seconds": 120,
                "calling_hours_start": 8,
                "calling_hours_end": 22,
                "calling_days": [0, 1, 2, 3, 4, 5, 6],
                "max_retries": 3
            }
        },
        {
            "name": "Pets24x7",
            "description": "24x7 Vet teleconsultation, pet grooming, vaccination, boarding and pet care assistant for pets24x7",
            "color": "#1f6feb",
            "profile": {
                "agent_name": "Dr. Priya Assistant",
                "company_name": "Pets24x7",
                "company_tagline": "24/7 Dedicated Pet Health, Grooming & Veterinary Care",
                "website_url": "https://pets24x7.com",
                "voice_speaker": "priya",
                "default_language": "en-IN",
                "agent_role": "veterinary care and pet services coordinator",
                "customer_noun": "pet parent",
                "objective": "Understand the pet parent requirements (dog/cat breed, health issue, grooming, vaccination schedule, emergency assistance or boarding) and connect or schedule a vet consultation.",
                "call_to_action": "Book a veterinary consultation or home grooming session with our certified pet experts.",
                "greeting_en": "Hello {name}! This is {agent} from Pets24x7. How can we help your furry friend today?",
                "greeting_hi": "नमस्ते {name}! मैं Pets24x7 से {agent} बोल रही हूँ। आज आपके प्यारे पेट (Pet) के लिए हम क्या सेवा दे सकते हैं?",
                "instructions": "Be compassionate, loving towards animals, quick and responsive. Ask what kind of pet they have (dog, cat, breed, age) and what service they need: immediate doctor consultation, routine checkup, vaccination, grooming, or boarding. For critical emergencies, advise visiting the nearest emergency animal clinic immediately.",
                "objection_handling": "Urgent symptoms: Prioritize immediate vet callback or video consult link within 10 minutes. Pricing: Transparent consultation and service packages with doorstep or clinic options.",
                "qualification_criteria": "Emergency/High: Sick pet needing urgent vet consult. Medium: Routine vaccination, grooming or boarding booking. Low: General pet diet or nutrition enquiry.",
                "forbidden_topics": "Never prescribe human medicine to pets over call or guarantee diagnoses without a licensed veterinarian evaluation.",
                "max_call_minutes": 5,
                "record_calls": True,
                "detect_voicemail": False,
                "transfer_number": "",
                "inbound_mode": "ai",
                "transfer_on_request": True,
                "after_hours_mode": "ai",
                "after_hours_message": "",
                "forward_fallback": "ai",
                "notify_missed_calls": True,
                "inbound_collect": ["name", "pet_type_and_breed", "service_needed", "city", "urgency_level", "phone", "email"],
                "team_members": [],
                "agent_password": ""
            },
            "automation": {
                "auto_dial_enabled": True,
                "speed_to_lead_enabled": True,
                "speed_to_lead_min_seconds": 15,
                "speed_to_lead_max_seconds": 60,
                "calling_hours_start": 0,
                "calling_hours_end": 24,
                "calling_days": [0, 1, 2, 3, 4, 5, 6],
                "max_retries": 3
            }
        }
    ]

    try:
        for ag in agents_to_create:
            existing = db.query(Agent).filter(Agent.name == ag["name"]).first()
            if existing:
                existing.description = ag["description"]
                existing.color = ag["color"]
                existing.profile = json.dumps(ag["profile"])
                existing.automation = json.dumps(ag["automation"])
                existing.status = "active"
                db.commit()
                print(f"Updated agent: {ag['name']} (ID: {existing.id})")
            else:
                agent = Agent(
                    name=ag["name"],
                    description=ag["description"],
                    color=ag["color"],
                    status="active",
                    profile=json.dumps(ag["profile"]),
                    automation=json.dumps(ag["automation"]),
                )
                db.add(agent)
                db.commit()
                print(f"Created agent: {ag['name']} (ID: {agent.id})")

        kb_data = [
            ("Wedeazzy", "Wedeazzy Services & Packages", "wedeazzy_services.txt", """Wedeazzy Services & Packages:
- Wedding Venues: Banquet halls, destination resorts, farmhouses, 5-star hotels across India (Jaipur, Udaipur, Goa, Mumbai, Delhi-NCR, Bangalore).
- Catering: Multi-cuisine catering starting from INR 1,200/plate up to luxury buffet INR 3,500/plate.
- Decor & Themes: Floral mandap, royal Rajasthani, minimalist pastel, luxury crystal, and destination beach wedding setups.
- Photography & Cinematography: Pre-wedding shoots, 4K wedding cinema, drone coverage, and candid albums.
- Complete Wedding Coordination: Dedicated on-ground event manager, hospitality desk, guest logistics, and artist bookings.
- Process: Form submission -> AI call qualification -> Free 1-on-1 consultation with wedding planner -> Customized quotation & venue recce."""),

            ("Hotelzz", "Hotelzz Overview & Policy", "hotelzz_overview.txt", """Hotelzz Overview & Booking Policy:
- Properties: Boutique hotels, luxury suites, and business transit hotels across all major metro cities and tourist hubs.
- Amenities: High-speed Wi-Fi, 24/7 in-room dining, swimming pool, wellness spa, gym, and complimentary gourmet breakfast.
- Corporate & Group Bookings: Special discounted slab rates for corporate tie-ups, wedding guest room blocks (10+ rooms), and event banquet halls.
- Banquet & Conference: Soundproof conference halls, banquet venues for 50 to 500 guests with AV equipment and event catering.
- Check-in / Check-out: Standard check-in at 2:00 PM, check-out at 11:00 AM. Early check-in subject to availability.
- Cancellation Policy: Free cancellation up to 24 hours before check-in date."""),

            ("Pets24x7", "Pets24x7 Services & Care", "pets24x7_services.txt", """Pets24x7 Care & Tele-health Services:
- 24x7 Vet Consultation: Instant video and voice consultation with licensed certified veterinary doctors for dogs, cats, and exotic pets.
- Emergency Care: Guidance on immediate first-aid for poisoning, trauma, vomiting, or seizures, with direct dispatch to nearest emergency clinic.
- At-Home Pet Grooming: Bath, de-shedding, tick & flea treatment, nail trimming, ear cleaning, and styling at your doorstep.
- Pet Boarding & Daycare: Cage-free boarding facilities with CCTV access, verified caregivers, and customized diet plans.
- Vaccination & Routine Checkups: Rabies, DHPPiL, Anti-Rabies, Tricat vaccinations delivered at home with digital vaccination passports.
- Nutrition & Diet Plans: Custom diet guidance for puppies, adult dogs, allergy-prone pets, and senior animals.""")
        ]

        for ag_name, title, filename, content in kb_data:
            ag = db.query(Agent).filter(Agent.name == ag_name).first()
            if not ag:
                continue
            doc = db.query(Document).filter(Document.agent_id == ag.id, Document.filename == filename).first()
            if not doc:
                doc = Document(
                    title=title,
                    filename=filename,
                    content_type="text/plain",
                    size_bytes=len(content),
                    chars=len(content),
                    chunk_count=1,
                    status="ready",
                    embedded=False,
                    agent_id=ag.id
                )
                db.add(doc)
                db.commit()
                chunk = DocumentChunk(
                    document_id=doc.id,
                    position=0,
                    text=content
                )
                db.add(chunk)
                db.commit()
                print(f"Added knowledge document for {ag_name}: {title}")

        agents = db.query(Agent).all()
        print("\n--- Current All Agents in DB ---")
        for a in agents:
            print(f"ID {a.id}: {a.name} | {a.color} | Status: {a.status}")

    finally:
        db.close()

if __name__ == "__main__":
    seed()

