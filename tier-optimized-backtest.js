/**
 * Tier-Optimized Backtest
 * Use median peak time for each entry price tier as forced exit time
 */

const fs = require('fs');
const path = require('path');

const DATA_DIR = '/workspace/workspaces/connie/polymarket-data';
const FILLS_DIR = path.join(DATA_DIR, 'fills');
const INDEX_FILE = path.join(DATA_DIR, 'moneyline-index.json');

const PREGAME_WINDOW_HOURS = 24;
const GAME_DURATION_MINUTES = 180;
const TARGET_MULT = 2.0;
const FRICTION_BUY = 0.005;
const FRICTION_SELL = 0.005;
const FEE = 0.001;

const index = JSON.parse(fs.readFileSync(INDEX_FILE, 'utf8'));
const indexMap = new Map(index.map(g => [g.slug, g]));

function getPrice(fill) {
    const maker = Number(fill.makerAmountFilled);
    const taker = Number(fill.takerAmountFilled);
    if (fill.makerAssetId === '0') return maker / taker;
    if (fill.takerAssetId === '0') return taker / maker;
    return null;
}

// First pass: collect peak times for each tier
const tierData = {
    'A': { entries: [], peakTimes: [] },  // <0.30
    'B': { entries: [], peakTimes: [] },  // 0.30-0.35
    'C': { entries: [], peakTimes: [] }   // 0.35-0.40
};

const files = fs.readdirSync(FILLS_DIR).filter(f => f.endsWith('-fills.json'));

for (const file of files) {
    const slug = file.replace('-fills.json', '');
    const gameInfo = indexMap.get(slug);
    if (!gameInfo) continue;
    
    let fillsData;
    try {
        fillsData = JSON.parse(fs.readFileSync(path.join(FILLS_DIR, file), 'utf8'));
    } catch { continue; }
    if (!fillsData?.fills?.length) continue;
    
    const startTime = new Date(gameInfo.startTime).getTime();
    const pregameStart = startTime - PREGAME_WINDOW_HOURS * 60 * 60 * 1000;
    const gameEnd = startTime + GAME_DURATION_MINUTES * 60 * 1000;
    
    for (const outcome of gameInfo.outcomes) {
        const outcomeFills = fillsData.fills.filter(f => f.outcome === outcome);
        
        const pregameFills = outcomeFills.filter(f => {
            const ts = Number(f.timestamp) * 1000;
            return ts >= pregameStart && ts < startTime;
        }).map(f => {
            const price = getPrice(f);
            return price && price > 0 && price < 1 ? { ts: Number(f.timestamp) * 1000, price } : null;
        }).filter(Boolean);
        
        const ingameFills = outcomeFills.filter(f => {
            const ts = Number(f.timestamp) * 1000;
            return ts >= startTime && ts < gameEnd;
        }).map(f => {
            const price = getPrice(f);
            const ts = Number(f.timestamp) * 1000;
            return price && price > 0 && price < 1 ? { ts, price, min: (ts - startTime) / 60000 } : null;
        }).filter(Boolean);
        
        if (pregameFills.length === 0 || ingameFills.length === 0) continue;
        
        const entryFill = pregameFills.find(f => f.price < 0.40);
        if (!entryFill) continue;
        
        const entryPrice = entryFill.price;
        
        // Find peak time
        let maxPrice = entryPrice;
        let peakTime = 0;
        for (const fill of ingameFills) {
            if (fill.price > maxPrice) {
                maxPrice = fill.price;
                peakTime = fill.min;
            }
        }
        
        // Determine tier
        let tier;
        if (entryPrice < 0.30) tier = 'A';
        else if (entryPrice < 0.35) tier = 'B';
        else tier = 'C';
        
        tierData[tier].entries.push({
            slug, outcome, entryPrice, maxPrice, peakTime,
            pregameFills, ingameFills
        });
        tierData[tier].peakTimes.push(peakTime);
    }
}

// Calculate median peak times for each tier
function median(arr) {
    if (arr.length === 0) return 0;
    const sorted = [...arr].sort((a, b) => a - b);
    return sorted[Math.floor(sorted.length / 2)];
}

const medianPeakTimes = {
    'A': median(tierData['A'].peakTimes),
    'B': median(tierData['B'].peakTimes),
    'C': median(tierData['C'].peakTimes)
};

console.log('=' .repeat(70));
console.log('TIER PEAK TIME STATISTICS');
console.log('='.repeat(70));
console.log('\n| Tier | Samples | Mean Peak Time | Median Peak Time |');
console.log('|------|---------|----------------|------------------|');
for (const [tier, label] of [['A', '<0.30'], ['B', '0.30-0.35'], ['C', '0.35-0.40']]) {
    const times = tierData[tier].peakTimes;
    if (times.length === 0) continue;
    const mean = times.reduce((s, t) => s + t, 0) / times.length;
    const med = median(times);
    console.log(`| ${label.padEnd(11)} | ${String(times.length).padStart(7)} | ${mean.toFixed(0).padStart(14)}min | ${med.toFixed(0).padStart(16)}min |`);
}

console.log('\n📊 Median Peak Times to use for forced exit:');
console.log(`   Tier A (<0.30):    ${medianPeakTimes['A'].toFixed(0)} min`);
console.log(`   Tier B (0.30-0.35): ${medianPeakTimes['B'].toFixed(0)} min`);
console.log(`   Tier C (0.35-0.40): ${medianPeakTimes['C'].toFixed(0)} min`);

// Backtest function
function backtest(entries, targetMult, forcedExitMin, tierName) {
    let wins = 0;
    let totalRoi = 0;
    const results = [];
    
    for (const e of entries) {
        const entryPriceWithFriction = e.entryPrice * (1 + FRICTION_BUY);
        let exitPrice = null;
        let exitType = 'force';
        
        for (const fill of e.ingameFills) {
            if (fill.price >= entryPriceWithFriction * targetMult / (1 - FRICTION_SELL)) {
                exitPrice = fill.price * (1 - FRICTION_SELL);
                exitType = 'target';
                break;
            }
            if (fill.min >= forcedExitMin && exitType !== 'target') {
                exitPrice = fill.price * (1 - FRICTION_SELL);
                exitType = 'forced';
                break;
            }
        }
        
        if (!exitPrice && e.ingameFills.length > 0) {
            exitPrice = e.ingameFills[e.ingameFills.length - 1].price * (1 - FRICTION_SELL);
            exitType = 'end';
        } else if (!exitPrice) {
            exitPrice = entryPriceWithFriction * 0.5;
            exitType = 'nodata';
        }
        
        const roi = (exitPrice / entryPriceWithFriction) - 1 - FEE;
        if (roi > 0) wins++;
        totalRoi += roi;
        results.push({ roi, exitType });
    }
    
    const n = entries.length;
    if (n === 0) return null;
    
    return {
        tier: tierName,
        samples: n,
        winRate: (wins / n * 100).toFixed(2),
        avgRoi: (totalRoi / n * 100).toFixed(2),
        exitDist: {
            target: results.filter(r => r.exitType === 'target').length,
            forced: results.filter(r => r.exitType === 'forced').length,
            end: results.filter(r => r.exitType === 'end').length
        }
    };
}

console.log('\n' + '='.repeat(70));
console.log('BACKTEST: ORIGINAL 165min vs TIER-OPTIMIZED EXIT');
console.log('='.repeat(70));

// Original strategy: 165min for all
console.log('\n--- ORIGINAL: 165min forced exit for all tiers ---');
console.log('| Tier | N | WinRate | ROI | Target | Forced | End |');
console.log('|------|---|---------|-----|--------|--------|-----|');

for (const [tier, label] of [['A', '<0.30'], ['B', '0.30-0.35'], ['C', '0.35-0.40']]) {
    const result = backtest(tierData[tier].entries, 2.0, 165, label);
    if (result) {
        console.log(`| ${label.padEnd(11)} | ${result.samples} | ${result.winRate}% | ${result.avgRoi}% | ${result.exitDist.target} | ${result.exitDist.forced} | ${result.exitDist.end} |`);
    }
}

// Tier-optimized: use median peak time for each tier
console.log('\n--- TIER-OPTIMIZED: Use median peak time as forced exit ---');
console.log('| Tier | Exit | N | WinRate | ROI | Target | Forced | End |');
console.log('|------|------|---|---------|-----|--------|--------|-----|');

for (const [tier, label] of [['A', '<0.30'], ['B', '0.30-0.35'], ['C', '0.35-0.40']]) {
    const exitMin = medianPeakTimes[tier];
    const result = backtest(tierData[tier].entries, 2.0, exitMin, label);
    if (result) {
        console.log(`| ${label.padEnd(11)} | ${exitMin.toFixed(0).padStart(4)}m | ${result.samples} | ${result.winRate}% | ${result.avgRoi}% | ${result.exitDist.target} | ${result.exitDist.forced} | ${result.exitDist.end} |`);
    }
}

// Comparison: Combined (excluding B)
console.log('\n' + '='.repeat(70));
console.log('COMBINED STRATEGY COMPARISON');
console.log('='.repeat(70));

// Strategy 1: All tiers, 165min
const allEntries = [...tierData['A'].entries, ...tierData['B'].entries, ...tierData['C'].entries];
const result165 = backtest(allEntries, 2.0, 165, 'All@165');

// Strategy 2: Exclude B, 165min
const excludeB = [...tierData['A'].entries, ...tierData['C'].entries];
const resultExcludeB = backtest(excludeB, 2.0, 165, 'ExcludeB@165');

// Strategy 3: Tier-optimized exit (A@median, B excluded, C@median)
function backtestTierOptimized(entriesA, entriesC, exitA, exitC) {
    let wins = 0;
    let totalRoi = 0;
    let n = 0;
    
    const runBacktest = (entries, forcedExitMin) => {
        for (const e of entries) {
            const entryPriceWithFriction = e.entryPrice * (1 + FRICTION_BUY);
            let exitPrice = null;
            
            for (const fill of e.ingameFills) {
                if (fill.price >= entryPriceWithFriction * 2.0 / (1 - FRICTION_SELL)) {
                    exitPrice = fill.price * (1 - FRICTION_SELL);
                    break;
                }
                if (fill.min >= forcedExitMin) {
                    exitPrice = fill.price * (1 - FRICTION_SELL);
                    break;
                }
            }
            
            if (!exitPrice && e.ingameFills.length > 0) {
                exitPrice = e.ingameFills[e.ingameFills.length - 1].price * (1 - FRICTION_SELL);
            } else if (!exitPrice) {
                exitPrice = entryPriceWithFriction * 0.5;
            }
            
            const roi = (exitPrice / entryPriceWithFriction) - 1 - FEE;
            if (roi > 0) wins++;
            totalRoi += roi;
            n++;
        }
    };
    
    runBacktest(entriesA, exitA);
    runBacktest(entriesC, exitC);
    
    if (n === 0) return null;
    return {
        samples: n,
        winRate: (wins / n * 100).toFixed(2),
        avgRoi: (totalRoi / n * 100).toFixed(2)
    };
}

const resultTierOpt = backtestTierOptimized(
    tierData['A'].entries, 
    tierData['C'].entries,
    medianPeakTimes['A'],
    medianPeakTimes['C']
);

console.log('\n| Strategy | N | WinRate | ROI | Participation |');
console.log('|----------|---|---------|-----|---------------|');
console.log(`| All tiers @165min | ${result165.samples} | ${result165.winRate}% | ${result165.avgRoi}% | ${(result165.samples/793*100).toFixed(1)}% |`);
console.log(`| Exclude B @165min | ${resultExcludeB.samples} | ${resultExcludeB.winRate}% | ${resultExcludeB.avgRoi}% | ${(resultExcludeB.samples/793*100).toFixed(1)}% |`);
console.log(`| Tier-optimized (A@${medianPeakTimes['A'].toFixed(0)}m, C@${medianPeakTimes['C'].toFixed(0)}m, no B) | ${resultTierOpt.samples} | ${resultTierOpt.winRate}% | ${resultTierOpt.avgRoi}% | ${(resultTierOpt.samples/793*100).toFixed(1)}% |`);

// Also test what happens if we use median exit for B (to see if it helps)
const resultBMedian = backtest(tierData['B'].entries, 2.0, medianPeakTimes['B'], 'B@median');
console.log(`\n📊 Tier B alone with median exit (${medianPeakTimes['B'].toFixed(0)}min):`);
if (resultBMedian) {
    console.log(`   N=${resultBMedian.samples}, WinRate=${resultBMedian.winRate}%, ROI=${resultBMedian.avgRoi}%`);
}

console.log('\n' + '='.repeat(70));
console.log('CONCLUSION');
console.log('='.repeat(70));
