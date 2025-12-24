#!/bin/bash
# Regenerate tokens for free users (for autoscaling test)

echo "Regenerating Firebase ID tokens for free users..."
echo "Note: Tokens expire after 1 hour"
echo ""

python3 get_jwt_tokens.py --environment cloud --user-type free --multi-free

if [ $? -eq 0 ]; then
    echo ""
    echo "✓ Tokens regenerated successfully"
    echo "You can now run: python test_autoscaling_usage.py"
else
    echo ""
    echo "✗ Token generation failed"
    echo "Make sure Firebase credentials are configured"
    exit 1
fi
