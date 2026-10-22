# Multiple comparisons

Test 20 hypotheses at the 5% level and one will look significant by luck alone. In our
calibration tables each price bucket has its own confidence interval, so a few "significant"
buckets are expected even for perfectly calibrated data (we saw exactly this in a synthetic
test). Remedies: pre-declare a small set of tests, report all of them, correct the threshold
(Bonferroni / false discovery rate), and above all confirm on data not used to find the effect.
