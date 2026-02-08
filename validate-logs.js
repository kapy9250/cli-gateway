#!/usr/bin/env node
/**
 * validate-logs.js - Paper Runner EventLog 验证脚本
 * 
 * 检查项:
 * 1. positionShares 最终归零
 * 2. ΣcashflowDelta == totals.cashflow
 * 3. 单场总买入 ≤ $20
 */

const fs = require('fs');

const MAX_BUY_PER_GAME = 20; // $20 限制

function validateLog(logPath) {
  const content = fs.readFileSync(logPath, 'utf8');
  const events = JSON.parse(content);
  
  const errors = [];
  const warnings = [];
  
  // 按 slug 分组追踪
  const gameStats = new Map(); // slug -> { shares, cashflowSum, totalBuy }
  let totals = null;
  
  for (const event of events) {
    const { type, slug } = event;
    
    // 初始化游戏统计
    if (slug && !gameStats.has(slug)) {
      gameStats.set(slug, { shares: 0, cashflowSum: 0, totalBuy: 0 });
    }
    
    const stats = slug ? gameStats.get(slug) : null;
    
    switch (type) {
      case 'FILL': {
        const { shares, cashflowDelta, side } = event;
        if (stats) {
          // BUY 增加 shares，SELL 减少
          stats.shares += (side === 'BUY' ? shares : -shares);
          stats.cashflowSum += cashflowDelta;
          if (side === 'BUY' && cashflowDelta < 0) {
            stats.totalBuy += Math.abs(cashflowDelta);
          }
        }
        break;
      }
      
      case 'FORCE_EXIT': {
        const { shares, cashflowDelta } = event;
        if (stats) {
          stats.shares -= shares; // 强制退出减少持仓
          stats.cashflowSum += cashflowDelta;
        }
        break;
      }
      
      case 'RUN_END':
      case 'TOTALS': {
        totals = event.totals || event;
        break;
      }
    }
  }
  
  // 验证 1: positionShares 最终归零
  for (const [slug, stats] of gameStats) {
    if (Math.abs(stats.shares) > 0.0001) {
      errors.push(`[${slug}] positionShares 未归零: ${stats.shares}`);
    }
  }
  
  // 验证 2: ΣcashflowDelta == totals.cashflow
  if (totals && totals.cashflow !== undefined) {
    let totalCashflow = 0;
    for (const stats of gameStats.values()) {
      totalCashflow += stats.cashflowSum;
    }
    if (Math.abs(totalCashflow - totals.cashflow) > 0.01) {
      errors.push(`cashflow 不匹配: Σ=${totalCashflow.toFixed(2)}, totals=${totals.cashflow}`);
    }
  }
  
  // 验证 3: 单场总买入 ≤ $20
  for (const [slug, stats] of gameStats) {
    if (stats.totalBuy > MAX_BUY_PER_GAME) {
      errors.push(`[${slug}] 总买入超限: $${stats.totalBuy.toFixed(2)} > $${MAX_BUY_PER_GAME}`);
    }
  }
  
  return { errors, warnings, gameStats, totals };
}

// CLI 入口
if (require.main === module) {
  const logPath = process.argv[2];
  if (!logPath) {
    console.error('Usage: node validate-logs.js <log.json>');
    process.exit(1);
  }
  
  try {
    const result = validateLog(logPath);
    
    console.log('=== Paper Runner Log Validation ===\n');
    console.log(`Games: ${result.gameStats.size}`);
    
    if (result.errors.length === 0) {
      console.log('\n✅ All checks passed!');
    } else {
      console.log(`\n❌ ${result.errors.length} error(s):`);
      result.errors.forEach(e => console.log(`  • ${e}`));
      process.exit(1);
    }
    
    if (result.warnings.length > 0) {
      console.log(`\n⚠️ ${result.warnings.length} warning(s):`);
      result.warnings.forEach(w => console.log(`  • ${w}`));
    }
  } catch (err) {
    console.error('Failed to validate:', err.message);
    process.exit(1);
  }
}

module.exports = { validateLog };
