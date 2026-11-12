'use strict';
// Built-in sample listings so the whole app can be tried with no accounts.
// Everything from this source is labelled DEMO in the interface.

const hours = (h) => new Date(Date.now() + h * 3600e3).toISOString();

const SAMPLES = [
  { category: 'test_equipment', title: 'Tektronix TDS2024B 200MHz 4 Channel Oscilloscope Powers On With Probes', price: 185, isAuction: true, bids: 3, endsIn: 9, shipping: 28, sold: [520, 480, 560, 495, 610, 450, 530, 505, 575] },
  { category: 'test_equipment', title: 'Fluke 87V True RMS Multimeter untested as-is', price: 60, isAuction: false, shipping: 9, sold: [235, 210, 250, 228, 240, 219] },
  { category: 'power_tools', title: 'Festool OF 1400 EQ Router with Systainer Made in Germany', price: 240, isAuction: false, shipping: 22, sold: [520, 480, 455, 540, 500, 465] },
  { category: 'power_tools', title: 'Milwaukee M18 Fuel Hammer Drill 2804-20 Bare Tool', price: 115, isAuction: false, shipping: 12, sold: [120, 110, 125, 118, 105] },
  { category: 'vintage_watches', title: 'Omega Seamaster Automatic Cal 565 Original Dial Running', price: 310, isAuction: true, bids: 7, endsIn: 30, shipping: 8, sold: [780, 690, 850, 720, 810, 760, 900, 640] },
  { category: 'vintage_watches', title: 'Rolex Submariner style replica homage watch', price: 80, isAuction: false, shipping: 6, sold: [] },
  { category: 'scientific_devices', title: 'Zeiss Axiostar Plus Trinocular Microscope Powers On University Surplus', price: 290, isAuction: true, bids: 1, endsIn: 52, shipping: 45, sold: [900, 760, 1100, 820, 870] },
  { category: 'scientific_devices', title: 'Mettler Toledo XS205 Analytical Balance', price: 120, isAuction: true, bids: 0, endsIn: 70, shipping: 30, sold: [1450, 1300] },
  { category: 'prepper_items', title: 'Big Berkey Water Filter System 2.25 Gallon Sealed Unopened', price: 95, isAuction: false, shipping: 14, sold: [240, 265, 230, 255, 250, 270, 245, 238] },
  { category: 'medical_lab_equipment', title: 'Thermo Forma 900 Series -86C Ultra Low Freezer Working Condition', price: 450, isAuction: true, bids: 2, endsIn: 18, shipping: 350, sold: [2400, 1900, 2750, 2200, 2100] },
  { category: 'fine_art', title: 'Estate art collection lot of signed and numbered lithographs', price: 140, isAuction: true, bids: 4, endsIn: 40, shipping: 35, sold: [] },
  { category: 'heavy_equipment', title: 'Bobcat S185 Skid Steer Loader Low Hours Runs Drives', price: 9800, isAuction: true, bids: 11, endsIn: 26, shipping: 900, sold: [18500, 16900, 21000, 17500, 19800] },
];

const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 40);

async function search(ctx, term, category) {
  return SAMPLES.filter((s) => s.category === category).map((s) => ({
    id: `demo_${slug(s.title)}`,
    source: 'demo',
    category,
    title: s.title,
    price: s.price,
    isAuction: s.isAuction,
    buyNow: s.isAuction ? null : s.price,
    bids: s.bids ?? null,
    endsAt: s.endsIn ? hours(s.endsIn) : null,
    url: 'https://example.com/demo-listing',
    image: null,
    sellerRating: 99.2,
    shipping: s.shipping,
    condition: null,
    location: null,
    demoSold: s.sold,
  }));
}

async function comps(ctx, q, listing) {
  return (listing.demoSold || []).map((price) => ({ title: listing.title, price, kind: 'sold' }));
}

module.exports = { name: 'demo', label: 'Demo data', needsKeys: false, search, comps, SAMPLES };
