/**
 * Optimal Exit Analysis
 * For each tier:
 * 1. Find optimal target price (maximize ROI)
 * 2. For trades that don't hit target, find optimal forced exit time window
 */

const fs = require('fs');
const path = require('path');

const DATA_DIR = '/workspace/workspaces/connie/polymarket-data';
const FILLS_DIR = path.join(DATA_DIR, 'fills');
const index = JSON.parse(fs.readFileSync(path.join(DATA_DIR, 'moneyline-index.json'), 'utf8'));
const indexMap = new Map(index.map(g => [g.slug, g]));

function getPrice(fill) {
    const maker = Number(fill.makerAmountFilled);
    const taker = Number(fill.takerAmountFilled);
    if (fill.makerAssetId === '0') return maker / taker;
    if (fill.takerAssetId === '0') return taker / maker;
    return null;
}

// Collect all entry opportunities with full price trajectory
const tierData = { A: [], B: [], C: [] };

const files = fs.readdirSync(FILLS_DIR).filter(f => f.endsWith('-fills.json'));

for (const file of files) {
    const slug = file.replace('-fills.json', '');
    const gameInfo = indexMap.get(slug);
    if (!gameInfo) continue;
    
    let fillsData;
    try { fillsData = JSON.parse(fs.readFileSync(path.join(FILLS_DIR, file), 'utf8')); }
    catch { continue; }
    if (!fillsData?.fills?.length) continue;
    
    const startTime = new Date(gameInfo.startTime).getTime();
    const pregameStart = startTime - 24 * 60 * 60 * 1000;
    const gameEnd = startTime + 180 * 60 * 1000;
    
    for (const outcome of gameInfo.outcomes) {
        const outcomeFills = fillsData.fills.filter(f => f.outcome === outcome);
        
        const pregameFills = outcomeFills.filter(f => {
            const ts = Number(f.timestamp) * 1000;
            return ts >= pregameStart && ts < startTime;
        }).map(f => {
            const price = getPrice(f);
            return price && price > 0 && price < 1 ? { price } : null;
        }).filter(Boolean);
        
        const ingameFills = outcomeFills.filter(f => {
            const ts = Number(f.timestamp) * 1000;
            return ts >= startTime && ts < gameEnd;
        }).map(f => {
            const price = getPrice(f);
            const ts = Number(f.timestamp) * 1000;
            return price && price > 0 && price < 1 ? { 
                price, 
                min: (ts - startTime) / 60000 
            } : null;
        }).filter(Boolean);
        
        if (pregameFills.length === 0 || ingameFills.length === 0) continue;
        
        const entryFill = pregameFills.find(f => f.price < 0.40);
        if (!entryFill) continue;
        
        const entryPrice = entryFill.price;
        
        // Find max price and its time
        let maxPrice = entryPrice;
        let maxPriceTime = 0;
        for (const fill of ingameFills) {
            if (fill.price > maxPrice) {
                maxPrice = fill.price;
                maxPriceTime = fill.min;
            }
        }
        
        let tier;
        if (entryPrice < 0.30) tier = 'A';
        else if (entryPrice < 0.35) tier = 'B';
        else tier = 'C';
        
        tierData[tier].push({ 
            entryPrice, 
            maxPrice, 
            maxPriceTime,
            ingameFills 
        });
    }
}

// Analyze each tier
console.log('='.repeat(70));
console.log('OPTIMAL EXIT ANALYSIS');
console.log('='.repeat(70));

for (const [tier, label] of [['A', '<0.30'], ['B', '0.30-0.35'], ['C', '0.35-0.40']]) {
    const data = tierData[tier];
    if (data.length === 0) continue;
    
    console.log(`\n${'─'.repeat(70)}`);
    console.log(`TIER ${label} (${data.length} samples)`);
    console.log('─'.repeat(70));
    
    // 1. Optimal target price analysis
    // Test different fixed target prices
    const targetPrices = [0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90];
    
    console.log('\n📊 Part 1: Optimal Target Price (fixed price target)');
    console.log('| Target | Hit Rate | Avg ROI (if hit) | Avg ROI (all) |');
    console.log('|--------|----------|------------------|---------------|');
    
    for (const target of targetPrices) {
        let hits = 0;
        let totalRoiIfHit = 0;
        
        for (const d of data) {
            if (d.maxPrice >= target) {
                hits++;
                totalRoiIfHit += (target / d.entryPrice - 1) * 100;
            }
        }
        
        const hitRate = (hits / data.length * 100).toFixed(1);
        const avgRoiIfHit = hits > 0 ? (totalRoiIfHit / hits).toFixed(1) : '-';
        // Expected ROI = hit rate * ROI if hit
        const expectedRoi = hits > 0 ? ((hits / data.length) * (totalRoiIfHit / hits)).toFixed(1) : '0';
        
        console.log(`| ${target.toFixed(2).padStart(6)} | ${hitRate.padStart(8)}% | ${avgRoiIfHit.toString().padStart(16)}% | ${expectedRoi.padStart(13)}% |`);
    }
    
    // Find optimal: maximize expected ROI
    let bestTarget = 0, bestExpectedRoi = -Infinity;
    for (const target of targetPrices) {
        let hits = 0, totalRoiIfHit = 0;
        for (const d of data) {
            if (d.maxPrice >= target) {
                hits++;
                totalRoiIfHit += (target / d.entryPrice - 1) * 100;
            }
        }
        const expectedRoi = hits > 0 ? (hits / data.length) * (totalRoiIfHit / hits) : 0;
        if (expectedRoi > bestExpectedRoi) {
            bestExpectedRoi = expectedRoi;
            bestTarget = target;
        }
    }
    console.log(`\n⭐ 最佳目标价格: ${bestTarget.toFixed(2)} (预期ROI: ${bestExpectedRoi.toFixed(1)}%)`);
    
    // Also analyze as multiplier
    console.log('\n📊 Part 1b: Optimal Target as Multiplier');
    const multipliers = [1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0, 2.5];
    console.log('| Mult | Hit Rate | Avg ROI (all) |');
    console.log('|------|----------|---------------|');
    
    for (const mult of multipliers) {
        let hits = 0;
        for (const d of data) {
            if (d.maxPrice >= d.entryPrice * mult) hits++;
        }
        const hitRate = (hits / data.length * 100).toFixed(1);
        const expectedRoi = ((mult - 1) * 100 * hits / data.length).toFixed(1);
        console.log(`| ${mult.toFixed(1)}x | ${hitRate.padStart(8)}% | ${expectedRoi.padStart(13)}% |`);
    }
    
    // 2. Optimal forced exit time for trades that don't hit target
    console.log('\n📊 Part 2: Optimal Forced Exit Time Window');
    console.log('(For trades that don\'t hit 2x target)');
    
    const timeWindows = [
        [30, 60], [45, 75], [60, 90], [60, 120], 
        [90, 120], [120, 150], [120, 165], [150, 180]
    ];
    
    // Filter to trades that don't hit 2x
    const noHit2x = data.filter(d => d.maxPrice < d.entryPrice * 2);
    
    console.log(`\nTrades not hitting 2x: ${noHit2x.length}/${data.length}`);
    console.log('| Time Window | Avg Exit Price | Avg ROI |');
    console.log('|-------------|----------------|---------|');
    
    for (const [start, end] of timeWindows) {
        let totalExitPrice = 0, totalRoi = 0, count = 0;
        
        for (const d of noHit2x) {
            // Find best price in time window
            const windowFills = d.ingameFills.filter(f => f.min >= start && f.min <= end);
            if (windowFills.length === 0) continue;
            
            const maxInWindow = Math.max(...windowFills.map(f => f.price));
            totalExitPrice += maxInWindow;
            totalRoi += (maxInWindow / d.entryPrice - 1) * 100;
            count++;
        }
        
        if (count > 0) {
            const avgExitPrice = (totalExitPrice / count).toFixed(4);
            const avgRoi = (totalRoi / count).toFixed(1);
            console.log(`| ${start}-${end}min | ${avgExitPrice.padStart(14)} | ${avgRoi.padStart(7)}% |`);
        }
    }
    
    // Find when max price typically occurs for non-2x-hitters
    const maxTimes = noHit2x.map(d => d.maxPriceTime);
    const medianTime = maxTimes.sort((a, b) => a - b)[Math.floor(maxTimes.length / 2)];
    const meanTime = maxTimes.reduce((s, t) => s + t, 0) / maxTimes.length;
    
    console.log(`\n⭐ 未达2x的交易，高点时间: 均值=${meanTime.toFixed(0)}min, 中位=${medianTime.toFixed(0)}min`);
}

console.log('\n' + '='.repeat(70));
console.log('SUMMARY RECOMMENDATIONS');
console.log('='.repeat(70));
