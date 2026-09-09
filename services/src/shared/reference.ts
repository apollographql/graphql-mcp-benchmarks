/**
 * Reference tables — the fixed vocabularies fixture data is drawn from.
 *
 * These are the tables REST denormalizes inline (the `lookup` shape) and GraphQL
 * exposes as scalars. Airport objects are ~12 fields each and a Flight carries
 * two of them, which is a large share of the -fat payload — mirroring GitHub
 * inlining a full `repo` object on both `head` and `base`.
 *
 * `utcOffsetMinutes` is a fixed standard-time offset. Real DST handling is
 * deliberately out of scope: it would add a dependency and vary by date, and the
 * benchmark needs byte-identical fixtures forever. Noted so nobody mistakes it
 * for an oversight.
 */

export interface Airport {
  iataCode: string;
  icaoCode: string;
  name: string;
  city: string;
  region: string;
  countryCode: string;
  timeZone: string;
  utcOffsetMinutes: number;
  coordinates: { latitude: number; longitude: number };
  terminals: string[];
}

export const AIRPORTS: readonly Airport[] = [
  { iataCode: 'SFO', icaoCode: 'KSFO', name: 'San Francisco International Airport', city: 'San Francisco', region: 'CA', countryCode: 'US', timeZone: 'America/Los_Angeles', utcOffsetMinutes: -480, coordinates: { latitude: 37.6213, longitude: -122.379 }, terminals: ['1', '2', '3', 'I'] },
  { iataCode: 'ORD', icaoCode: 'KORD', name: "O'Hare International Airport", city: 'Chicago', region: 'IL', countryCode: 'US', timeZone: 'America/Chicago', utcOffsetMinutes: -360, coordinates: { latitude: 41.9742, longitude: -87.9073 }, terminals: ['1', '2', '3', '5'] },
  { iataCode: 'JFK', icaoCode: 'KJFK', name: 'John F. Kennedy International Airport', city: 'New York', region: 'NY', countryCode: 'US', timeZone: 'America/New_York', utcOffsetMinutes: -300, coordinates: { latitude: 40.6413, longitude: -73.7781 }, terminals: ['1', '4', '5', '7', '8'] },
  { iataCode: 'LAX', icaoCode: 'KLAX', name: 'Los Angeles International Airport', city: 'Los Angeles', region: 'CA', countryCode: 'US', timeZone: 'America/Los_Angeles', utcOffsetMinutes: -480, coordinates: { latitude: 33.9416, longitude: -118.4085 }, terminals: ['1', '2', '3', '4', '5', '6', '7', 'B'] },
  { iataCode: 'DFW', icaoCode: 'KDFW', name: 'Dallas/Fort Worth International Airport', city: 'Dallas', region: 'TX', countryCode: 'US', timeZone: 'America/Chicago', utcOffsetMinutes: -360, coordinates: { latitude: 32.8998, longitude: -97.0403 }, terminals: ['A', 'B', 'C', 'D', 'E'] },
  { iataCode: 'DEN', icaoCode: 'KDEN', name: 'Denver International Airport', city: 'Denver', region: 'CO', countryCode: 'US', timeZone: 'America/Denver', utcOffsetMinutes: -420, coordinates: { latitude: 39.8561, longitude: -104.6737 }, terminals: ['A', 'B', 'C'] },
  { iataCode: 'ATL', icaoCode: 'KATL', name: 'Hartsfield–Jackson Atlanta International Airport', city: 'Atlanta', region: 'GA', countryCode: 'US', timeZone: 'America/New_York', utcOffsetMinutes: -300, coordinates: { latitude: 33.6407, longitude: -84.4277 }, terminals: ['N', 'S', 'T', 'E', 'F'] },
  { iataCode: 'SEA', icaoCode: 'KSEA', name: 'Seattle–Tacoma International Airport', city: 'Seattle', region: 'WA', countryCode: 'US', timeZone: 'America/Los_Angeles', utcOffsetMinutes: -480, coordinates: { latitude: 47.4502, longitude: -122.3088 }, terminals: ['A', 'B', 'C', 'D', 'N', 'S'] },
  { iataCode: 'BOS', icaoCode: 'KBOS', name: 'Logan International Airport', city: 'Boston', region: 'MA', countryCode: 'US', timeZone: 'America/New_York', utcOffsetMinutes: -300, coordinates: { latitude: 42.3656, longitude: -71.0096 }, terminals: ['A', 'B', 'C', 'E'] },
  { iataCode: 'MIA', icaoCode: 'KMIA', name: 'Miami International Airport', city: 'Miami', region: 'FL', countryCode: 'US', timeZone: 'America/New_York', utcOffsetMinutes: -300, coordinates: { latitude: 25.7959, longitude: -80.287 }, terminals: ['D', 'E', 'F', 'G', 'H', 'J'] },
  { iataCode: 'PHX', icaoCode: 'KPHX', name: 'Phoenix Sky Harbor International Airport', city: 'Phoenix', region: 'AZ', countryCode: 'US', timeZone: 'America/Phoenix', utcOffsetMinutes: -420, coordinates: { latitude: 33.4342, longitude: -112.0116 }, terminals: ['3', '4'] },
  { iataCode: 'IAH', icaoCode: 'KIAH', name: 'George Bush Intercontinental Airport', city: 'Houston', region: 'TX', countryCode: 'US', timeZone: 'America/Chicago', utcOffsetMinutes: -360, coordinates: { latitude: 29.9902, longitude: -95.3368 }, terminals: ['A', 'B', 'C', 'D', 'E'] },
  { iataCode: 'MSP', icaoCode: 'KMSP', name: 'Minneapolis–Saint Paul International Airport', city: 'Minneapolis', region: 'MN', countryCode: 'US', timeZone: 'America/Chicago', utcOffsetMinutes: -360, coordinates: { latitude: 44.882, longitude: -93.2218 }, terminals: ['1', '2'] },
  { iataCode: 'DTW', icaoCode: 'KDTW', name: 'Detroit Metropolitan Wayne County Airport', city: 'Detroit', region: 'MI', countryCode: 'US', timeZone: 'America/New_York', utcOffsetMinutes: -300, coordinates: { latitude: 42.2162, longitude: -83.3554 }, terminals: ['M', 'W'] },
  { iataCode: 'CLT', icaoCode: 'KCLT', name: 'Charlotte Douglas International Airport', city: 'Charlotte', region: 'NC', countryCode: 'US', timeZone: 'America/New_York', utcOffsetMinutes: -300, coordinates: { latitude: 35.214, longitude: -80.9431 }, terminals: ['A', 'B', 'C', 'D', 'E'] },
  { iataCode: 'LAS', icaoCode: 'KLAS', name: 'Harry Reid International Airport', city: 'Las Vegas', region: 'NV', countryCode: 'US', timeZone: 'America/Los_Angeles', utcOffsetMinutes: -480, coordinates: { latitude: 36.084, longitude: -115.1537 }, terminals: ['1', '3'] },
  { iataCode: 'PDX', icaoCode: 'KPDX', name: 'Portland International Airport', city: 'Portland', region: 'OR', countryCode: 'US', timeZone: 'America/Los_Angeles', utcOffsetMinutes: -480, coordinates: { latitude: 45.5898, longitude: -122.5951 }, terminals: ['A', 'B', 'C', 'D', 'E'] },
  { iataCode: 'SLC', icaoCode: 'KSLC', name: 'Salt Lake City International Airport', city: 'Salt Lake City', region: 'UT', countryCode: 'US', timeZone: 'America/Denver', utcOffsetMinutes: -420, coordinates: { latitude: 40.7899, longitude: -111.9791 }, terminals: ['A', 'B'] },
  { iataCode: 'EWR', icaoCode: 'KEWR', name: 'Newark Liberty International Airport', city: 'Newark', region: 'NJ', countryCode: 'US', timeZone: 'America/New_York', utcOffsetMinutes: -300, coordinates: { latitude: 40.6895, longitude: -74.1745 }, terminals: ['A', 'B', 'C'] },
  { iataCode: 'BWI', icaoCode: 'KBWI', name: 'Baltimore/Washington International Airport', city: 'Baltimore', region: 'MD', countryCode: 'US', timeZone: 'America/New_York', utcOffsetMinutes: -300, coordinates: { latitude: 39.1774, longitude: -76.6684 }, terminals: ['A', 'B', 'C', 'D', 'E'] },
  { iataCode: 'SAN', icaoCode: 'KSAN', name: 'San Diego International Airport', city: 'San Diego', region: 'CA', countryCode: 'US', timeZone: 'America/Los_Angeles', utcOffsetMinutes: -480, coordinates: { latitude: 32.7338, longitude: -117.1933 }, terminals: ['1', '2'] },
  { iataCode: 'TPA', icaoCode: 'KTPA', name: 'Tampa International Airport', city: 'Tampa', region: 'FL', countryCode: 'US', timeZone: 'America/New_York', utcOffsetMinutes: -300, coordinates: { latitude: 27.9755, longitude: -82.5332 }, terminals: ['A', 'C', 'E', 'F'] },
  { iataCode: 'AUS', icaoCode: 'KAUS', name: 'Austin–Bergstrom International Airport', city: 'Austin', region: 'TX', countryCode: 'US', timeZone: 'America/Chicago', utcOffsetMinutes: -360, coordinates: { latitude: 30.1975, longitude: -97.6664 }, terminals: ['B', 'S'] },
  { iataCode: 'HNL', icaoCode: 'PHNL', name: 'Daniel K. Inouye International Airport', city: 'Honolulu', region: 'HI', countryCode: 'US', timeZone: 'Pacific/Honolulu', utcOffsetMinutes: -600, coordinates: { latitude: 21.3187, longitude: -157.9224 }, terminals: ['1', '2', '3'] },
];

export const AIRPORTS_BY_IATA: ReadonlyMap<string, Airport> = new Map(
  AIRPORTS.map((a) => [a.iataCode, a]),
);

export interface Carrier {
  iataCode: string;
  icaoCode: string;
  name: string;
  callsign: string;
}

export const CARRIERS: readonly Carrier[] = [
  { iataCode: 'UA', icaoCode: 'UAL', name: 'United Airlines', callsign: 'UNITED' },
  { iataCode: 'AA', icaoCode: 'AAL', name: 'American Airlines', callsign: 'AMERICAN' },
  { iataCode: 'DL', icaoCode: 'DAL', name: 'Delta Air Lines', callsign: 'DELTA' },
  { iataCode: 'AS', icaoCode: 'ASA', name: 'Alaska Airlines', callsign: 'ALASKA' },
];

export const CARRIERS_BY_IATA: ReadonlyMap<string, Carrier> = new Map(
  CARRIERS.map((c) => [c.iataCode, c]),
);

/** Codeshare partners — drawn from outside CARRIERS so the two never collide. */
export const CODESHARE_PARTNERS: readonly string[] = ['LH', 'AC', 'NH', 'BA', 'AF', 'SQ'];

export interface AircraftModel {
  code: string;
  manufacturer: string;
  name: string;
  seatCount: number;
  rangeNauticalMiles: number;
}

/**
 * Model codes double as the M2 join key: Fleet owns `Aircraft.model`, Personnel
 * owns `CrewMember.typeRatings[].model`, and the series task must match them.
 */
export const AIRCRAFT_MODELS: readonly AircraftModel[] = [
  { code: 'B738', manufacturer: 'Boeing', name: '737-800', seatCount: 166, rangeNauticalMiles: 2935 },
  { code: 'B739', manufacturer: 'Boeing', name: '737-900ER', seatCount: 179, rangeNauticalMiles: 2950 },
  { code: 'A320', manufacturer: 'Airbus', name: 'A320-200', seatCount: 150, rangeNauticalMiles: 3300 },
  { code: 'A321', manufacturer: 'Airbus', name: 'A321-200', seatCount: 190, rangeNauticalMiles: 3200 },
  { code: 'B752', manufacturer: 'Boeing', name: '757-200', seatCount: 176, rangeNauticalMiles: 3915 },
  { code: 'B77W', manufacturer: 'Boeing', name: '777-300ER', seatCount: 350, rangeNauticalMiles: 7370 },
  { code: 'B789', manufacturer: 'Boeing', name: '787-9', seatCount: 290, rangeNauticalMiles: 7635 },
  { code: 'A359', manufacturer: 'Airbus', name: 'A350-900', seatCount: 315, rangeNauticalMiles: 8100 },
];

export const AIRCRAFT_MODELS_BY_CODE: ReadonlyMap<string, AircraftModel> = new Map(
  AIRCRAFT_MODELS.map((m) => [m.code, m]),
);

// ── Coded-value tables ───────────────────────────────────────────────────────
// REST serves these as a triple (value, code, description); GraphQL serves the
// enum alone. precedent: nearly every airline and telco API.

export const FLIGHT_STATUS = {
  SCHEDULED: { code: 1, description: 'Scheduled — on plan' },
  BOARDING: { code: 2, description: 'Boarding in progress' },
  DEPARTED: { code: 3, description: 'Departed — airborne' },
  DELAYED: { code: 4, description: 'Delayed — awaiting inbound aircraft' },
  LANDED: { code: 5, description: 'Landed — arrived at gate' },
  CANCELLED: { code: 6, description: 'Cancelled — see rebooking options' },
} as const;

export const ADVISORY_SEVERITY = {
  ADVISORY: { code: 1, description: 'Advisory — informational, no operational limit' },
  RESTRICTION: { code: 2, description: 'Restriction — operational limitation in effect' },
  GROUNDING: { code: 3, description: 'Grounding — aircraft not airworthy until cleared' },
} as const;

export const CREW_ROLE = {
  CAPTAIN: { code: 1, description: 'Captain — pilot in command' },
  FIRST_OFFICER: { code: 2, description: 'First Officer — second in command' },
  PURSER: { code: 3, description: 'Purser — lead cabin crew' },
  CABIN: { code: 4, description: 'Cabin Crew' },
} as const;

export const DELAY_REASONS = {
  'AC-INBOUND': 'Late arrival of inbound aircraft',
  'WX-ORIGIN': 'Weather at origin airport',
  'WX-DEST': 'Weather at destination airport',
  'ATC-FLOW': 'Air traffic control flow restriction',
  'CREW-LEGAL': 'Crew duty-time limitation',
  'MAINT-UNSCH': 'Unscheduled maintenance',
  'PAX-BOARDING': 'Extended passenger boarding',
} as const;

/** Deterministic surname/given-name pools for crew records. */
export const GIVEN_NAMES: readonly string[] = [
  'Alex', 'Bailey', 'Casey', 'Devon', 'Emery', 'Finley', 'Gray', 'Harper',
  'Iris', 'Jordan', 'Kai', 'Logan', 'Morgan', 'Noor', 'Oakley', 'Parker',
  'Quinn', 'Reese', 'Sage', 'Tatum', 'Umber', 'Vale', 'Wren', 'Yael',
];

export const FAMILY_NAMES: readonly string[] = [
  'Abara', 'Bhatt', 'Castellanos', 'Duarte', 'Eriksen', 'Fontaine', 'Gallego',
  'Haddad', 'Ibarra', 'Jorgensen', 'Kowalski', 'Lindqvist', 'Moreau', 'Nakamura',
  'Okonkwo', 'Pereira', 'Quintero', 'Rasmussen', 'Sandoval', 'Tanaka',
  'Ueda', 'Vasquez', 'Whitfield', 'Zawadzki',
];

/** Crew bases — a subset of AIRPORTS, since crew are based at hubs. */
export const CREW_BASES: readonly string[] = ['SFO', 'ORD', 'JFK', 'LAX', 'DFW', 'DEN'];
