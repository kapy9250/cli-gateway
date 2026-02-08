/**
 * Feature Analysis for Polymarket NHL Betting Strategy
 * 
 * Tasks:
 * 1. Trading density analysis - compare high vs low density pregame periods
 * 2. Price path features - early breakout vs false breakout patterns
 */

const fs = require('fs');
const path = require('path');

const DATA_DIR = '/workspace/workspaces/connie/polymarket-data';
const FILLS_DIR = path.join(DATA_DIR, 'fills');
const INDEX_FILE = path.join(DATA_DIR, 'moneyline-index.json');

// Configuration
const PREGAME_WINDOW_HOURS = 24;
const GAME_DURATION_MINUTES = 180; // 3h proxy
const ENTRY_THRESHOLD = 0.40;
const TARGET_MULT = 2.0;
const FIXED_EXIT_MIN = 165;
const FRICTION_BUY = 0.005;
const FRICTION_SELL = 0.005;
const FEE = 0.001;

// Load index
const index = JSON.parse(fs.readFileSync(INDEX_FILE, 'utf8'));
const indexMap = new Map(index.map(g => [g.slug, g]));

function loadFills(slug) {
    const filePath = path.join(FILLS_DIR, `${slug}-fills.json`);
    if (!fs.existsSync(filePath)) return null;
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function computePrice(fill) {
    const maker = Number(fill.makerAmountFilled);
    const taker = Number(fill.takerAmountFilled);
    if (maker === 0) return null;
    return taker / maker;
}

function analyzeGame(slug) {
    const gameInfo = indexMap.get(slug);
    if (!gameInfo) return null;
    
    const fillsData = loadFills(slug);
    if (!fillsData || !fillsData.fills || fillsData.fills.length === 0) return null;
    
    const startTime = new Date(gameInfo.startTime).getTime();
    const pregameStart = startTime - PREGAME_WINDOW_HOURS * 60 * 60 * 1000;
    const gameEnd = startTime + GAME_DURATION_MINUTES * 60 * 1000;
    
    // Separate fills by outcome and time period
    const results = {};
    
    for (const outcome of gameInfo.outcomes) {
        const outcomeFills = fillsData.fills.filter(f => f.outcome === outcome);
        
        // Pregame fills
        const pregameFills = outcomeFills.filter(f => {
            const ts = Number(f.timestamp) * 1000;
            return ts >= pregameStart && ts < startTime;
        }).map(f => ({
            timestamp: Number(f.timestamp) * 1000,
            price: computePrice(f)
        })).filter(f => f.price !== null);
        
        // Ingame fills  
        const ingameFills = outcomeFills.filter(f => {
            const ts = Number(f.timestamp) * 1000;
            return ts >= startTime && ts < gameEnd;
        }).map(f => ({
            timestamp: Number(f.timestamp) * 1000,
            price: computePrice(f),
            minutesSinceStart: (Number(f.timestamp) * 1000 - startTime) / 60000
        })).filter(f => f.price !== null);
        
        if (pregameFills.length === 0) continue;
        
        // Get entry price (first trigger under threshold)
        const entryFill = pregameFills.find(f => f.price < ENTRY_THRESHOLD);
        if (!entryFill) continue;
        
        const entryPrice = entryFill.price * (1 + FRICTION_BUY);
        
        // Calculate pregame trading density (fills per hour)
        const pregameDurationHours = (startTime - pregameStart) / (60 * 60 * 1000);
        const tradingDensity = pregameFills.length / pregameDurationHours;
        
        // Analyze price path features
        const first60minFills = ingameFills.filter(f => f.minutesSinceStart <= 60);
        const maxPriceFirst60 = first60minFills.length > 0 ? 
            Math.max(...first60minFills.map(f => f.price)) : entryPrice;
        const earlyBreakout = maxPriceFirst60 >= entryPrice * 1.5; // Hit 1.5x in first 60min
        
        // Check for false breakout (hit high then fell back)
        let maxPriceSoFar = entryPrice;
        let hadFalseBreakout = false;
        for (const fill of ingameFills) {
            if (fill.price > maxPriceSoFar) {
                maxPriceSoFar = fill.price;
            }
            // If we hit 1.3x then dropped below 1.1x, it's a false breakout
            if (maxPriceSoFar >= entryPrice * 1.3 && fill.price < entryPrice * 1.1) {
                hadFalseBreakout = true;
                break;
            }
        }
        
        // Calculate outcome using strategy 1 (2x/165min)
        let exitPrice = null;
        let exitType = 'force';
        
        for (const fill of ingameFills) {
            if (fill.price >= entryPrice * TARGET_MULT) {
                exitPrice = fill.price * (1 - FRICTION_SELL);
                exitType = 'target';
                break;
            }
            if (fill.minutesSinceStart >= FIXED_EXIT_MIN) {
                exitPrice = fill.price * (1 - FRICTION_SELL);
                exitType = '165min';
                break;
            }
        }
        
        // If no exit, use last available price or entry
        if (!exitPrice) {
            const lastFill = ingameFills[ingameFills.length - 1];
            exitPrice = lastFill ? lastFill.price * (1 - FRICTION_SELL) : entryPrice * 0.9;
            exitType = 'force';
        }
        
        // Calculate ROI
        const roi = (exitPrice / entryPrice) - 1 - FEE;
        const won = roi > 0;
        
        results[outcome] = {
            slug,
            outcome,
            entryPrice,
            exitPrice,
            exitType,
            roi,
            won,
            tradingDensity,
            earlyBreakout,
            hadFalseBreakout,
            pregameFillCount: pregameFills.length,
            ingameFillCount: ingameFills.length,
            maxPriceFirst60
        };
    }
    
    return results;
}

// Collect all results
console.log('Starting feature analysis...');
const allResults = [];
const fillFiles = fs.readdirSync(FILLS_DIR).filter(f => f.endsWith('-fills.json'));

for (const file of fillFiles) {
    const slug = file.replace('-fills.json', '');
    const gameResults = analyzeGame(slug);
    if (gameResults) {
        for (const outcome of Object.values(gameResults)) {
            allResults.push(outcome);
        }
    }
}

console.log(`\nAnalyzed ${allResults.length} entry opportunities\n`);

// Analysis 1: Trading Density
console.log('='.repeat(60));
console.log('ANALYSIS 1: TRADING DENSITY');
console.log('='.repeat(60));

const densities = allResults.map(r => r.tradingDensity).sort((a, b) => a - b);
const medianDensity = densities[Math.floor(densities.length / 2)];

const highDensity = allResults.filter(r => r.tradingDensity >= medianDensity);
const lowDensity = allResults.filter(r => r.tradingDensity < medianDensity);

function calcStats(data) {
    if (data.length === 0) return { count: 0, winRate: 0, avgRoi: 0 };
    const wins = data.filter(r => r.won).length;
    const totalRoi = data.reduce((s, r) => s + r.roi, 0);
    return {
        count: data.length,
        winRate: (wins / data.length * 100).toFixed(2),
        avgRoi: (totalRoi / data.length * 100).toFixed(2)
    };
}

const highStats = calcStats(highDensity);
const lowStats = calcStats(lowDensity);

console.log(`\nMedian trading density: ${medianDensity.toFixed(2)} fills/hour\n`);
console.log('| Category | Count | Win Rate | Avg ROI |');
console.log('|----------|-------|----------|---------|');
console.log(`| High Density (≥${medianDensity.toFixed(1)}) | ${highStats.count} | ${highStats.winRate}% | ${highStats.avgRoi}% |`);
console.log(`| Low Density (<${medianDensity.toFixed(1)}) | ${lowStats.count} | ${lowStats.winRate}% | ${lowStats.avgRoi}% |`);

// Further bucketing by quartiles
const q1 = densities[Math.floor(densities.length * 0.25)];
const q3 = densities[Math.floor(densities.length * 0.75)];

const bucket1 = allResults.filter(r => r.tradingDensity < q1);
const bucket2 = allResults.filter(r => r.tradingDensity >= q1 && r.tradingDensity < medianDensity);
const bucket3 = allResults.filter(r => r.tradingDensity >= medianDensity && r.tradingDensity < q3);
const bucket4 = allResults.filter(r => r.tradingDensity >= q3);

console.log('\n--- By Quartiles ---');
console.log('| Quartile | Density Range | Count | Win Rate | Avg ROI |');
console.log('|----------|---------------|-------|----------|---------|');
const b1 = calcStats(bucket1);
const b2 = calcStats(bucket2);
const b3 = calcStats(bucket3);
const b4 = calcStats(bucket4);
console.log(`| Q1 (lowest) | <${q1.toFixed(1)} | ${b1.count} | ${b1.winRate}% | ${b1.avgRoi}% |`);
console.log(`| Q2 | ${q1.toFixed(1)}-${medianDensity.toFixed(1)} | ${b2.count} | ${b2.winRate}% | ${b2.avgRoi}% |`);
console.log(`| Q3 | ${medianDensity.toFixed(1)}-${q3.toFixed(1)} | ${b3.count} | ${b3.winRate}% | ${b3.avgRoi}% |`);
console.log(`| Q4 (highest) | ≥${q3.toFixed(1)} | ${b4.count} | ${b4.winRate}% | ${b4.avgRoi}% |`);

// Analysis 2: Price Path Features
console.log('\n' + '='.repeat(60));
console.log('ANALYSIS 2: PRICE PATH FEATURES');
console.log('='.repeat(60));

const earlyBreakouts = allResults.filter(r => r.earlyBreakout);
const noEarlyBreakout = allResults.filter(r => !r.earlyBreakout);

const ebStats = calcStats(earlyBreakouts);
const nebStats = calcStats(noEarlyBreakout);

console.log('\n--- Early Breakout (hit 1.5x in first 60min) ---');
console.log('| Category | Count | Win Rate | Avg ROI |');
console.log('|----------|-------|----------|---------|');
console.log(`| Early Breakout | ${ebStats.count} | ${ebStats.winRate}% | ${ebStats.avgRoi}% |`);
console.log(`| No Early Breakout | ${nebStats.count} | ${nebStats.winRate}% | ${nebStats.avgRoi}% |`);

// False breakout analysis
const falseBreakouts = allResults.filter(r => r.hadFalseBreakout);
const noFalseBreakout = allResults.filter(r => !r.hadFalseBreakout);

const fbStats = calcStats(falseBreakouts);
const nfbStats = calcStats(noFalseBreakout);

console.log('\n--- False Breakout (hit 1.3x then dropped below 1.1x) ---');
console.log('| Category | Count | Win Rate | Avg ROI |');
console.log('|----------|-------|----------|---------|');
console.log(`| False Breakout | ${fbStats.count} | ${fbStats.winRate}% | ${fbStats.avgRoi}% |`);
console.log(`| No False Breakout | ${nfbStats.count} | ${nfbStats.winRate}% | ${nfbStats.avgRoi}% |`);

// Combined analysis: density + path
console.log('\n' + '='.repeat(60));
console.log('ANALYSIS 3: COMBINED FEATURES');
console.log('='.repeat(60));

const highDensityNoFalseBreakout = allResults.filter(r => r.tradingDensity >= medianDensity && !r.hadFalseBreakout);
const combinedStats = calcStats(highDensityNoFalseBreakout);

console.log('\n--- High Density + No False Breakout ---');
console.log(`| Count: ${combinedStats.count} | Win Rate: ${combinedStats.winRate}% | Avg ROI: ${combinedStats.avgRoi}% |`);

// Exit type distribution
console.log('\n' + '='.repeat(60));
console.log('EXIT TYPE DISTRIBUTION');
console.log('='.repeat(60));

const exitTypes = {};
for (const r of allResults) {
    exitTypes[r.exitType] = (exitTypes[r.exitType] || 0) + 1;
}

console.log('\n| Exit Type | Count | Percentage |');
console.log('|-----------|-------|------------|');
for (const [type, count] of Object.entries(exitTypes)) {
    console.log(`| ${type} | ${count} | ${(count / allResults.length * 100).toFixed(1)}% |`);
}

// Summary statistics
console.log('\n' + '='.repeat(60));
console.log('SUMMARY');
console.log('='.repeat(60));

const overallStats = calcStats(allResults);
console.log(`\nOverall: ${allResults.length} opportunities, ${overallStats.winRate}% win rate, ${overallStats.avgRoi}% avg ROI`);

// Key findings
console.log('\n📊 KEY FINDINGS:');
console.log(`1. Trading Density: Q4 (highest) vs Q1 (lowest) - Win Rate: ${b4.winRate}% vs ${b1.winRate}%, ROI: ${b4.avgRoi}% vs ${b1.avgRoi}%`);
console.log(`2. Early Breakout: Yes vs No - Win Rate: ${ebStats.winRate}% vs ${nebStats.winRate}%, ROI: ${ebStats.avgRoi}% vs ${nebStats.avgRoi}%`);
console.log(`3. False Breakout: Yes vs No - Win Rate: ${fbStats.winRate}% vs ${nfbStats.winRate}%, ROI: ${fbStats.avgRoi}% vs ${nfbStats.avgRoi}%`);

// Save results
const output = {
    analysisDate: new Date().toISOString(),
    totalOpportunities: allResults.length,
    tradingDensityAnalysis: {
        medianDensity,
        quartiles: { q1, median: medianDensity, q3 },
        byQuartile: { b1, b2, b3, b4 }
    },
    pricePathAnalysis: {
        earlyBreakout: ebStats,
        noEarlyBreakout: nebStats,
        falseBreakout: fbStats,
        noFalseBreakout: nfbStats
    },
    combinedAnalysis: {
        highDensityNoFalseBreakout: combinedStats
    },
    exitDistribution: exitTypes
};

fs.writeFileSync('/workspace/workspaces/manny/feature-analysis-results.json', JSON.stringify(output, null, 2));
console.log('\n✅ Results saved to /workspace/workspaces/manny/feature-analysis-results.json');
