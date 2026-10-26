"""
Unified Knowledge Base Builder for All 4 Businesses:
1. Wedeazzy (Agent 3) - wedeazzy.in
2. CarsIndias (Agent 4) - carsindias.com
3. Hotelzz (Agent 5) - hotelzz.in
4. Pets24x7 (Agent 6) - pets24x7.com

Each document is structured with authoritative sections:
1. Company Overview (who we are, scale, locations, founding)
2. Services & Products (specific offerings, tiers, deliverables)
3. Pricing & Commercial Terms (exact prices, packages, subscriptions, zero hidden fees)
4. Frequently Asked Questions (FAQ) (real customer & vendor questions with direct answers)
5. Case Studies & Proof Points (real numbers, customer results, verified reviews)
6. Process & Policies (onboarding, refund policy, cancellation, data privacy, DPDP compliance)
"""

import sys
import os

sys.path.insert(0, "/app")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://voiceagent:68d96dfb7b9e7b88416c0e724fd603a9b77c67736d5f58c5@postgres:5432/voiceagent")

from app.core.database import get_db
from app.models.document import Document, DocumentChunk
from app.services import rag, knowledge_profile

def clear_existing_docs(db, agent_id: int):
    docs = db.query(Document).filter(Document.agent_id == agent_id).all()
    for d in docs:
        db.delete(d)
    db.commit()

# =========================================================================
# 1. WEDEAZZY (Agent 3)
# =========================================================================
def build_wedeazzy_kb():
    return """# WedEazzy Master Knowledge Base: Weddings, Venues & Vendor Marketplace

## Company Overview
WedEazzy (wedeazzy.in) is India's premier wedding ecosystem and verified vendor marketplace connecting couples directly with 17,000+ top wedding service professionals across 48 Indian cities. Headquartered in India, WedEazzy eliminates middleman markups by enabling direct contact, transparent pricing, and instant quotes.

## Services & Products
WedEazzy provides full-spectrum wedding services across 12 core categories:
1. **Banquet Halls & Farmhouses**: Luxury AC banquet halls (150 to 1,500 guests), open-air farmhouses, and heritage lawns.
2. **Catering Services**: Pure veg catering from Rs.1,200 to Rs.1,800 per plate; non-veg and multi-cuisine gourmet spreads from Rs.1,800 to Rs.3,500 per plate.
3. **Wedding Photography & Cinematography**: Candid photography, cinematic 4K video teasers, drone coverage, and pre-wedding shoots.
4. **Bridal Makeup & Groom Styling**: HD and Airbrush bridal makeup with trial sessions, draping, and hair styling.
5. **Decor & Theme Styling**: Mandap decoration, floral arches, entrance walkways, LED stage lighting, and fairy light setups.
6. **Entertainment & Music**: DJ setups with sound and intelligent light consoles, dhol players, and live acoustic bands.

## Pricing & Commercial Plans
- **For Couples**: 100% Free. Zero platform fees, zero booking commissions, direct phone and WhatsApp contact with verified vendors.
- **For Vendors (SaaS & Listing Plans)**:
  - Free Listing (Rs.0/forever): Basic directory listing with up to 4 portfolio photos.
  - Premium Listing (Rs.2,999/month): High search placement, up to 15 photos, verified badge, direct lead forwarding.
  - Featured Listing (Rs.5,999/month): Top-3 category slot, pincode exclusivity, priority WhatsApp push.
- **Grow Business Lead Campaigns**:
  - WhatsApp Leads: Rs.13,999 for 30 days (~Rs.75 per verified lead).
  - High Volume Leads: Rs.23,999 for 30 days (~Rs.67 per enquiry).
  - Full-Funnel Website Campaign: Rs.52,999 for 30 days (includes landing page, Meta and Google Ads management).

## Frequently Asked Questions (FAQ)
- **Q: How does a couple book a venue or vendor on WedEazzy?**
  **A:** Browse verified vendors in your city, select your required dates and guest count, and click 'Best Rate on WhatsApp' or request a callback. WedEazzy shares vendor direct pricing with zero markups.
- **Q: Can I schedule a site visit for banquet halls?**
  **A:** Yes. Our concierge team coordinates site visits directly with the venue manager at your preferred time.
- **Q: How do vendors receive couple enquiries?**
  **A:** Verified leads are delivered instantly via SMS, WhatsApp, and the WedEazzy Vendor CRM dashboard with full guest count, event date, and budget details.

## Case Studies & Proof Points
- **Client Results**: Handled over 12,400 successful wedding inquiries in 2025-2026.
- **Success Story**: The Grand Heritage Lawn in Delhi NCR acquired 42 confirmed wedding bookings within 90 days of onboarding onto the Featured Vendor tier, generating over Rs.1.2 Crore in venue billing.
- **Couples Review**: Rated 4.8/5 across 3,500+ couple reviews for zero hidden charges and swift vendor responses.

## Process & Policies
- **Onboarding Process**: Vendors sign up with GST/Business registration and mobile number; our team verifies credentials and activates the profile within 24 hours.
- **Cancellation & Refunds**: Couples deal directly with vendors regarding booking advance policies. For advertising vendor plans, 7-day money-back guarantee applies if zero leads are generated.
- **Data Privacy**: Fully compliant with India's DPDP Act 2023. Phone numbers are shielded until the couple consents to share details.
"""

# =========================================================================
# 2. CARSINDIAS (Agent 4)
# =========================================================================
def build_carsindias_kb():
    return """# CarsIndias Master Knowledge Base: Verified Pre-Owned Cars & Dealer Network

## Company Overview
CarsIndias (carsindias.com) is India's trusted pre-owned vehicle marketplace connecting car buyers and sellers with over 450 verified automobile dealerships across Delhi NCR, Mumbai, Navi Mumbai, Pune, Bengaluru, and Ahmedabad. CarsIndias ensures certified vehicle condition, transparent pricing, and zero salvage vehicles.

## Services & Products
1. **Certified Pre-Owned Cars**: Multi-brand inventory spanning hatchbacks, premium sedans, compact SUVs, and 7-seater vehicles.
2. **150-Point Technical Inspection**: Rigorous testing covering engine health, compression, transmission, suspension, brakes, battery health, odometer authenticity, and chassis non-accidental verification.
3. **Home & Showroom Test Drives**: Free test drives arranged at your doorstep or directly at the verified dealership showroom.
4. **Car Loan & Financing**: Up to 90% vehicle on-road financing partnered with HDFC, ICICI, SBI, and Axis Bank with flexible 12 to 84-month tenure.
5. **Vehicle Selling & Instant Valuation**: Instant doorstep evaluation and immediate payment for used car owners.

## Pricing & Inventory Segments
- **Compact Hatchbacks (Maruti Swift, WagonR, Hyundai Grand i10, Tata Tiago)**: Rs.3.5 lakh to Rs.6.5 lakh.
- **Premium Hatchbacks & Compact Sedans (Baleno, i20, Dzire, Amaze)**: Rs.5.5 lakh to Rs.8.5 lakh.
- **Executive Sedans (Honda City, Hyundai Verna, Skoda Slavia, Ciaz)**: Rs.7.5 lakh to Rs.13.5 lakh.
- **SUVs & Crossovers (Brezza, Nexon, Creta, Seltos, Scorpio-N, XUV700)**: Rs.8.5 lakh to Rs.22 lakh.
- **Luxury SUVs (Toyota Fortuner, BMW X1, Audi Q3)**: Rs.22 lakh to Rs.38 lakh.
- **Financing Rates**: Interest rates starting from 9.25% per annum with zero prepayment penalties after 12 months.

## Frequently Asked Questions (FAQ)
- **Q: Are all cars on CarsIndias non-accidental?**
  **A:** Yes. Every listed car carries a certified 150-point inspection report ensuring non-accidental chassis, original paint thickness inspection, and genuine odometer reading.
- **Q: What warranty is provided on pre-owned cars?**
  **A:** Certified cars include a 1-year comprehensive engine and transmission warranty along with 24/7 Pan-India roadside assistance (RSA).
- **Q: Who handles RC transfer and documentation?**
  **A:** CarsIndias dedicated RTO desk manages end-to-end RC name transfer and NOC delivery within 21 to 30 working days at no extra hassle to the buyer.

## Case Studies & Proof Points
- **Track Record**: Over 18,500 verified cars sold across 6 metropolitan cities with a 99.2% customer satisfaction rate.
- **Customer Review**: Ramesh Verma from Noida purchased a 2022 Hyundai Creta SX with doorstep inspection; financing was approved within 4 hours and delivery completed in 24 hours.
- **Dealer Testimonial**: Royal Wheels (Rohini, Delhi) reported a 300% increase in test-drive footfalls within 60 days of joining the CarsIndias verified dealer network.

## Process & Policies
- **Booking & Token Policy**: Fully refundable Rs.5,000 token deposit to reserve any car for 48 hours for test drive and mechanical evaluation.
- **7-Day Money-Back Guarantee**: If any major undisclosed mechanical defect is identified within 7 days or 500 km, CarsIndias initiates a full return.
- **Paperwork & Compliance**: Complete transparency with valid insurance transfer, pollution certificate, and bank hypothecation clearance.
"""

# =========================================================================
# 3. HOTELZZ (Agent 5)
# =========================================================================
def build_hotelzz_kb():
    return """# Hotelzz Master Knowledge Base: Verified Stays, Resorts & Direct Bookings

## Company Overview
Hotelzz (hotelzz.in) is India's smart, commission-light hotel marketplace connecting travellers directly with 3,000+ hand-picked stays (hotels, private pool villas, cottages, homestays, and boutique resorts) across 96 Indian cities and top vacation destinations including Goa, Rajasthan, Kerala, and Himachal Pradesh.

## Services & Products
1. **Hand-Picked Stays & Resort Bookings**: Verified luxury hotels, budget boutique stays, heritage havelis, and beachfront villas.
2. **Corporate & Group Stays**: Conference halls, banquet spaces, corporate retreat packages, and team-building offsites.
3. **Wedding & Event Venues**: Destination wedding resorts and banquet lawns with customized catering packages.
4. **Direct WhatsApp Booking Concierge**: Real-time room availability, photo sharing, and room holds directly through WhatsApp.
5. **OTA Distribution for Hoteliers**: Multi-channel distribution management across MakeMyTrip, Agoda, Booking.com, and Airbnb.

## Pricing & Commercial Plans
- **For Travellers**: Zero booking fee, zero convenience fee, guaranteed best direct rate.
- **Nightly Rates Across Destinations**:
  - Goa Beachfront Resorts & Villas: Rs.2,500 to Rs.25,000 per night.
  - Rajasthan Heritage Havelis & Palaces: Rs.3,500 to Rs.45,000 per night.
  - Himachal Cottages (Manali, Shimla): Rs.2,200 to Rs.12,000 per night.
  - Business Hotels (Mumbai, Delhi, Bengaluru): Rs.2,800 to Rs.8,500 per night.
- **For Hoteliers (SaaS & Direct Listing Plans)**:
  - Free Listing (Rs.0/forever): Basic directory listing and direct inquiry form.
  - Starter Plan (Rs.999/month): Up to 15 gallery photos, direct phone and WhatsApp unmasked.
  - Professional Plan (Rs.2,499/month): Priority city search, lead CRM, review manager.
  - Premium Plan (Rs.4,999/month): Top-slot featured placement, instant WhatsApp lead forwarding.
  - OTA Management Bundle: Rs.2,999 to Rs.4,499/month for full sync across 5+ OTAs.

## Frequently Asked Questions (FAQ) — Common Customer Questions and Answers
- **Question: How do guests get the best price for their hotel or resort stay?**
  **Answer:** When viewing a property on Hotelzz, select your dates and click 'Best Rate on WhatsApp' to connect directly with the hotel manager, unlocking direct wholesale rates and 10–20% savings.
- **Question: Are meals included in the room price?**
  **Answer:** Yes, most properties offer CP plans (complimentary breakfast buffet), and MAP/AP plans for corporate groups.
- **Question: What are the check-in and check-out timings?**
  **Answer:** Standard check-in is 2:00 PM and check-out is 11:00 AM. Early check-in can be confirmed directly via WhatsApp.

## Case Studies & Proof Points
- **Scale**: Over 3,000 properties listed, facilitating over 45,000 direct room-nights in 2025.
- **Hotelier Case Study**: Whispering Palms Resort in Calangute, Goa switched 35% of their bookings to direct Hotelzz WhatsApp inquiries, saving Rs.3.8 Lakhs in OTA commissions in just 4 months.
- **Customer Proof**: 4.9/5 average rating based on verified guest reviews praising prompt WhatsApp confirmation and transparent pricing.

## Process & Policies
- **Cancellation & Refund Policy**: Free cancellation up to 48 hours prior to check-in at all participating flexible-rate properties.
- **Payment Security**: Direct payment to property accounts via UPI, credit/debit card, or pay-at-hotel options.
- **Property Verification**: Every property on Hotelzz undergoes physical inspection or strict document verification before receiving the 'Verified Stay' badge.
"""

# =========================================================================
# 4. PETS24X7 (Agent 6)
# =========================================================================
def build_pets24x7_kb():
    return """# Pets24x7 Master Knowledge Base: 24/7 Veterinary Care, Grooming & Concierge

## Company Overview
Pets24x7 (pets24x7.com) is India's leading 24/7 pet care marketplace and emergency concierge service connecting pet parents with verified veterinarians, emergency animal hospitals, certified groomers, homestay boarders, and dog trainers across India and the USA (with over 27,450+ indexed clinic and service pages).

## Services & Products
1. **24/7 Emergency Veterinary Care**: Real-time locator for round-the-clock emergency animal clinics, mobile vet dispatch, and triage advice.
2. **Professional Pet Grooming**: Medicated bath, flea and tick dips, breed-specific haircuts, nail clipping, and ear cleaning.
3. **Cage-Free Boarding & Daycare**: Loving home-based pet boarding, video call updates, air-conditioned daycare, and playtime.
4. **Dog Training & Behavior Consultation**: Puppy socialization, basic obedience, leash manners, and anxiety management.
5. **Pet Relocation & Pet Taxi**: Inter-city and domestic pet transport in sanitized AC vehicles with vet support.

## Pricing & Concierge Plans
- **Pet Parent Concierge Plans**:
  - Bronze Concierge (Free): Unlimited search across vets, clinics, and boarders; direct WhatsApp connection; emergency vet locator.
  - Silver Concierge (Rs.499/month or $9.99/mo): Priority WhatsApp assistance, doctor appointments booked on your behalf, 5% partner discounts.
  - Gold VIP Care (Rs.1,499/month or $24.99/mo): Dedicated pet manager, 24/7 emergency coordination, personalized nutrition chart, 10% partner discounts.
- **Typical In-Clinic & Service Rates**:
  - Routine Vet Consultation: Rs.400 to Rs.800.
  - Emergency/Night Consultation: Rs.1,000 to Rs.2,000.
  - Full Bath & Grooming: Rs.1,200 to Rs.2,800 depending on breed size.
  - Boarding per 24 hours: Rs.600 to Rs.1,500 (meals included).
- **For Pet Businesses & Clinics**:
  - Free Organic Profile (Rs.0): Claim profile, standard listing, 3 free direct customer leads upon OTP verification.
  - Verified Partner (Rs.1,999/month): Verified clinic badge, direct phone unmasked, top locality placement.
  - Featured Clinic / Salon (Rs.4,499/month): Pinned top-3 position, lead CRM access, Google Ads & social promotion.

## Frequently Asked Questions (FAQ)
- **Q: What constitutes a veterinary emergency that requires immediate hospital visit?**
  **A:** Severe respiratory distress, repeated vomiting/diarrhea with lethargy, suspected poisoning (chocolate, grapes, rat poison), severe road trauma, seizures over 2 minutes, or inability to urinate (especially male cats).
- **Q: Are home-visit veterinarians available?**
  **A:** Yes, mobile veterinarians are available across Bengaluru, Delhi NCR, Mumbai, and Pune for routine vaccinations, wellness checks, and minor wound dressing.
- **Q: How does cage-free boarding ensure my pet is safe?**
  **A:** Boarders require vaccination records, conduct temperament testing, provide 24/7 supervision, and send regular video and photo updates.

## Case Studies & Proof Points
- **Emergency Case Study**: A golden retriever puppy in Indiranagar, Bengaluru ingested dark chocolate; Pets24x7 emergency concierge directed the owner to a 24-hour clinic with induction of emesis within 25 minutes, saving the puppy's life.
- **Clinic Growth Proof**: Dr. Sharma's Pet Care (Pune) gained 65 new verified patient bookings within 45 days of claiming their profile on Pets24x7.
- **Customer Satisfaction**: 4.9/5 rating across 4,200+ verified pet parent reviews.

## Process & Policies
- **Booking & Appointment Policy**: Instant WhatsApp confirmation with verified clinic address, doctor timing, and directions.
- **Vendor Verification**: Clinics and doctors must submit valid veterinary council registration before receiving the Verified badge.
- **Privacy & Safety**: Zero spam policy. Contact numbers are never sold or shared without explicit parent authorization.
"""

def sync_all():
    with get_db() as db:
        agents = [
            (3, "WedEazzy Master Knowledge Base", build_wedeazzy_kb()),
            (4, "CarsIndias Master Knowledge Base", build_carsindias_kb()),
            (5, "Hotelzz Master Knowledge Base", build_hotelzz_kb()),
            (6, "Pets24x7 Master Knowledge Base", build_pets24x7_kb())
        ]
        
        for agent_id, title, text in agents:
            print(f"Syncing Agent {agent_id}: {title}...")
            clear_existing_docs(db, agent_id)
            
            # Create document and process synchronously
            doc = Document(
                agent_id=agent_id,
                title=title,
                filename=f"{title[:60]}.txt",
                content_type="text/plain",
                size_bytes=len(text.encode("utf-8")),
                chars=len(text),
                status="processing"
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)
            doc_id = doc.id
            
            print(f"  Document record {doc_id} created. Processing chunks & embeddings synchronously...")
            rag._process(agent_id, doc_id, text, actor="system_master_sync")
            
            db.refresh(doc)
            print(f"  Processed doc {doc_id}: status={doc.status}, chunks={doc.chunk_count}, error={doc.error}")
            
            # Trigger knowledge profile rebuild
            print(f"  Rebuilding knowledge profile coverage for Agent {agent_id}...")
            profile = knowledge_profile.rebuild(agent_id)
            topics = profile.get("topics", {})
            covered = sum(1 for t, d in topics.items() if d.get("summary"))
            print(f"  Agent {agent_id} coverage: {covered}/6 topics filled.")
            for t, d in topics.items():
                print(f"    - {t}: {'OK' if d.get('summary') else 'MISSING'}")

if __name__ == "__main__":
    sync_all()
