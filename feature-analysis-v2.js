/**
 * Feature Analysis v2 - Fixed price calculation
 */

const fs = require('fs');
const path = require('path');

const DATA_DIR = '/workspace/workspaces/connie/polymarket-data';
const FILLS_DIR = path.join(DATA_DIR, 'fills');
const INDEX_FILE = path.join(DATA_DIR, 'moneyline-index.json');

// Configuration
const PREGAME_WINDOW_HOURS = 24;
const GAME_DURATION_MINUTES = 180;
const ENTRY_THRESHOLD = 0.40;
const TARGET_MULT = 2.0;
const FIXED_EXIT_MIN = 165;
const FRICTION_BUY = 0.005;
const FRICTION_SELL = 0.005;
const FEE = 0.001;

const index = JSON.parse(fs.readFileSync(INDEX_FILE, 'utf8'));
const indexMap = new Map(index.map(g => [g.slug, g]));

// FIXED: Correct price calculation
function getPrice(fill) {
    const maker = Number(fill.makerAmountFilled);
    const taker = Number(fill.takerAmountFilled);
    if (fill.makerAssetId === '0') return maker / taker;  // maker is USDC
    if (fill.takerAssetId === '0') return taker / maker;  // taker is USDC
    return null;
}

function loadFills(slug) {
    const filePath = path.join(FILLS_DIR, `${slug}-fills.json`);
    if (!fs.existsSync(filePath)) return null;
    try {
        return JSON.parse(fs.readFileSync(filePath, 'utf8'));
    } catch { return null; }
}

function analyzeGame(slug) {
    const gameInfo = indexMap.get(slug);
    if (!gameInfo) return null;
    
    const fillsData = loadFills(slug);
    if (!fillsData?.fills?.length) return null;
    
    const startTime = new Date(gameInfo.startTime).getTime();
    const pregameStart = startTime - PREGAME_WINDOW_HOURS * 60 * 60 * 1000;
    const gameEnd = startTime + GAME_DURATION_MINUTES * 60 * 1000;
    
    const results = [];
    
    for (const outcome of gameInfo.outcomes) {
        const outcomeFills = fillsData.fills.filter(f => f.outcome === outcome);
        
        // Pregame fills with corrected price
        const pregameFills = outcomeFills.filter(f => {
            const ts = Number(f.timestamp) * 1000;
            return ts >= pregameStart && ts < startTime;
        }).map(f => {
            const price = getPrice(f);
            return price !== null && price > 0 && price < 1 ? {
                timestamp: Number(f.timestamp) * 1000,
                price
            } : null;
        }).filter(Boolean);
        
        // Ingame fills
        const ingameFills = outcomeFills.filter(f => {
            const ts = Number(f.timestamp) * 1000;
            return ts >= startTime && ts < gameEnd;
        }).map(f => {
            const price = getPrice(f);
            const ts = Number(f.timestamp) * 1000;
            return price !== null && price > 0 && price < 1 ? {
                timestamp: ts,
                price,
                minutesSinceStart: (ts - startTime) / 60000
            } : null;
        }).filter(Boolean);
        
        if (pregameFills.length === 0) continue;
        
        // Entry: first trigger under threshold
        const entryFill = pregameFills.find(f => f.price < ENTRY_THRESHOLD);
        if (!entryFill) continue;
        
        const entryPrice = entryFill.price * (1 + FRICTION_BUY);
        
        // Trading density (fills per hour in pregame)
        const pregameDurationHours = (startTime - pregameStart) / (60 * 60 * 1000);
        const tradingDensity = pregameFills.length / pregameDurationHours;
        
        // Price path features
        const first60 = ingameFills.filter(f => f.minutesSinceStart <= 60);
        const maxFirst60 = first60.length > 0 ? Math.max(...first60.map(f => f.price)) : entryPrice;
        const earlyBreakout = maxFirst60 >= entryPrice * 1.5;
        
        // False breakout detection
        let peakPrice = entryPrice;
        let hadFalseBreakout = false;
        for (const fill of ingameFills) {
            if (fill.price > peakPrice) peakPrice = fill.price;
            if (peakPrice >= entryPrice * 1.3 && fill.price < peakPrice * 0.85) {
                hadFalseBreakout = true;
                break;
            }
        }
        
        // Exit simulation (2x / 165min strategy)
        let exitPrice = null;
        let exitType = 'force';
        
        for (const fill of ingameFills) {
            if (fill.price >= entryPrice * TARGET_MULT / (1 - FRICTION_SELL)) {
                exitPrice = fill.price * (1 - FRICTION_SELL);
                exitType = 'target';
                break;
            }
            if (fill.minutesSinceStart >= FIXED_EXIT_MIN && exitType !== 'target') {
                exitPrice = fill.price * (1 - FRICTION_SELL);
                exitType = '165min';
                break;
            }
        }
        
        if (!exitPrice && ingameFills.length > 0) {
            const last = ingameFills[ingameFills.length - 1];
            exitPrice = last.price * (1 - FRICTION_SELL);
            exitType = 'force';
        } else if (!exitPrice) {
            exitPrice = entryPrice * 0.5;
            exitType = 'nodata';
        }
        
        const roi = (exitPrice / entryPrice) - 1 - FEE;
        
        results.push({
            slug,
            outcome,
            entryPrice,
            exitPrice,
            exitType,
            roi,
            won: roi > 0,
            tradingDensity,
            earlyBreakout,
            hadFalseBreakout,
            pregameFillCount: pregameFills.length,
            ingameFillCount: ingameFills.length,
            maxFirst60
        });
    }
    
    return results;
}

// Run analysis
console.log('Feature Analysis v2 (corrected price calculation)');
console.log('='.repeat(60));

const allResults = [];
const files = fs.readdirSync(FILLS_DIR).filter(f => f.endsWith('-fills.json'));

for (const file of files) {
    const slug = file.replace('-fills.json', '');
    const gameResults = analyzeGame(slug);
    if (gameResults) allResults.push(...gameResults);
}

console.log(`\nTotal opportunities: ${allResults.length}\n`);

function calcStats(data) {
    if (data.length === 0) return { count: 0, winRate: '0.00', avgRoi: '0.00' };
    const wins = data.filter(r => r.won).length;
    const totalRoi = data.reduce((s, r) => s + r.roi, 0);
    return {
        count: data.length,
        winRate: (wins / data.length * 100).toFixed(2),
        avgRoi: (totalRoi / data.length * 100).toFixed(2)
    };
}

// Analysis 1: Trading Density
console.log('=== ANALYSIS 1: TRADING DENSITY ===\n');
const densities = allResults.map(r => r.tradingDensity).sort((a, b) => a - b);
const medianD = densities[Math.floor(densities.length / 2)];
const q1 = densities[Math.floor(densities.length * 0.25)];
const q3 = densities[Math.floor(densities.length * 0.75)];

const buckets = [
    { name: 'Q1 (lowest)', data: allResults.filter(r => r.tradingDensity < q1) },
    { name: 'Q2', data: allResults.filter(r => r.tradingDensity >= q1 && r.tradingDensity < medianD) },
    { name: 'Q3', data: allResults.filter(r => r.tradingDensity >= medianD && r.tradingDensity < q3) },
    { name: 'Q4 (highest)', data: allResults.filter(r => r.tradingDensity >= q3) }
];

console.log(`Density quartiles: Q1<${q1.toFixed(1)}, Q2<${medianD.toFixed(1)}, Q3<${q3.toFixed(1)}, Q4≥${q3.toFixed(1)}`);
console.log('\n| Quartile | Count | Win Rate | Avg ROI |');
console.log('|----------|-------|----------|---------|');
for (const b of buckets) {
    const s = calcStats(b.data);
    console.log(`| ${b.name.padEnd(12)} | ${String(s.count).padStart(5)} | ${s.winRate.padStart(6)}% | ${s.avgRoi.padStart(7)}% |`);
}

// Analysis 2: Price Path
console.log('\n=== ANALYSIS 2: PRICE PATH FEATURES ===\n');

const earlyB = allResults.filter(r => r.earlyBreakout);
const noEarlyB = allResults.filter(r => !r.earlyBreakout);
const falseB = allResults.filter(r => r.hadFalseBreakout);
const noFalseB = allResults.filter(r => !r.hadFalseBreakout);

console.log('--- Early Breakout (hit 1.5x in first 60min) ---');
console.log(`| Yes | ${calcStats(earlyB).count} | ${calcStats(earlyB).winRate}% | ${calcStats(earlyB).avgRoi}% |`);
console.log(`| No  | ${calcStats(noEarlyB).count} | ${calcStats(noEarlyB).winRate}% | ${calcStats(noEarlyB).avgRoi}% |`);

console.log('\n--- False Breakout (peak then drop >15%) ---');
console.log(`| Yes | ${calcStats(falseB).count} | ${calcStats(falseB).winRate}% | ${calcStats(falseB).avgRoi}% |`);
console.log(`| No  | ${calcStats(noFalseB).count} | ${calcStats(noFalseB).winRate}% | ${calcStats(noFalseB).avgRoi}% |`);

// Exit distribution
console.log('\n=== EXIT TYPE DISTRIBUTION ===\n');
const exitTypes = {};
for (const r of allResults) exitTypes[r.exitType] = (exitTypes[r.exitType] || 0) + 1;
for (const [type, count] of Object.entries(exitTypes)) {
    console.log(`| ${type.padEnd(8)} | ${count} (${(count/allResults.length*100).toFixed(1)}%) |`);
}

// Overall stats
const overall = calcStats(allResults);
console.log('\n=== OVERALL ===');
console.log(`Total: ${overall.count}, Win Rate: ${overall.winRate}%, Avg ROI: ${overall.avgRoi}%`);
console.log(`Effective Yield: ${(parseFloat(overall.avgRoi) * allResults.length / 793).toFixed(2)}%`);

// Save
fs.writeFileSync('/workspace/workspaces/manny/feature-analysis-v2-results.json', JSON.stringify({
    totalOpportunities: allResults.length,
    overall,
    densityQuartiles: { q1, median: medianD, q3 },
    bucketStats: buckets.map(b => ({ name: b.name, ...calcStats(b.data) })),
    earlyBreakout: calcStats(earlyB),
    noEarlyBreakout: calcStats(noEarlyB),
    falseBreakout: calcStats(falseB),
    noFalseBreakout: calcStats(noFalseB),
    exitTypes
}, null, 2));
console.log('\n✅ Saved to feature-analysis-v2-results.json');
