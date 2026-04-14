#!/bin/bash
# Quick testing commands for PPW Auction Platform

echo "🧪 PPW Testing Quick Start"
echo "=========================="
echo ""

# Check if pytest is installed
if ! command -v pytest &> /dev/null; then
    echo "❌ pytest not found. Installing dependencies..."
    pip install -r requirements/dev.txt
fi

# Show menu
echo "Select an option:"
echo "1) Run all tests"
echo "2) Run tests with coverage report"
echo "3) Run authentication tests only"
echo "4) Run auction tests only"
echo "5) Run validators tests only"
echo "6) Run with verbose output"
echo "7) Run tests in parallel (fast)"
echo "8) Show coverage report"
echo ""

read -p "Enter choice (1-8): " choice


echo ""
echo "✅ Done!"
